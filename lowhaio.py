import asyncio
import ipaddress
import urllib.parse
import ssl
import socket

from aiodnsresolver import (
    TYPES,
    Resolver,
)


def streamed(data):
    async def _streamed():
        yield data
    return _streamed()


async def buffered(data):
    return b''.join([chunk async for chunk in data])


def identity_or_chunked_handler(transfer_encoding):
    return {
        b'chunked': chunked_handler,
        b'identity': identity_handler,
    }[transfer_encoding]


def Pool(
        dns_resolver=Resolver,
        ssl_context=ssl.create_default_context,
        recv_bufsize=16384,
        transfer_encoding_handler=identity_or_chunked_handler,
):

    loop = \
        asyncio.get_running_loop() if hasattr(asyncio, 'get_running_loop') else \
        asyncio.get_event_loop()
    ssl_context = ssl_context()
    dns_resolve, dns_resolver_clear_cache = dns_resolver()

    async def request(method, url, params=(), headers=(), body=streamed(b'')):
        parsed_url = urllib.parse.urlsplit(url)

        sock = get_sock()
        try:
            await connect(sock, parsed_url)

            if parsed_url.scheme == 'https':
                sock = tls_wrapped(sock, parsed_url.hostname)
                await tls_complete_handshake(loop, sock)

            await send_header(sock, method, parsed_url, params, headers)
            await send_body(sock, body)

            code, response_headers, body_handler, unprocessed = await recv_header(sock)
            response_body = response_body_generator(sock, body_handler,
                                                    response_headers, unprocessed)
            unprocessed = None  # So can be garbage collected
        except BaseException:
            sock.close()
            raise

        return code, response_headers, response_body

    def get_sock():
        sock = socket.socket(family=socket.AF_INET, type=socket.SOCK_STREAM,
                             proto=socket.IPPROTO_TCP)
        sock.setblocking(False)
        return sock

    async def connect(sock, parsed_url):
        host, _, port_specified = parsed_url.netloc.partition(':')
        scheme = parsed_url.scheme
        port = \
            port_specified if port_specified != '' else \
            443 if scheme == 'https' else \
            80
        try:
            ip_address = str(ipaddress.ip_address(host))
        except ValueError:
            ip_address = str((await dns_resolve(host, TYPES.A))[0])
        address = (ip_address, port)
        await loop.sock_connect(sock, address)

    def tls_wrapped(sock, host):
        return ssl_context.wrap_socket(sock,
                                       server_hostname=host,
                                       do_handshake_on_connect=False)

    async def send_header(sock, method, parsed_url, params, headers):
        outgoing_qs = urllib.parse.urlencode(params, doseq=True).encode()
        outgoing_path = urllib.parse.quote(parsed_url.path).encode()
        outgoing_path_qs = outgoing_path + \
            ((b'?' + outgoing_qs) if outgoing_qs != b'' else b'')
        header = \
            method + b' ' + outgoing_path_qs + b' HTTP/1.1\r\n' + \
            b'host:' + parsed_url.hostname.encode('idna') + b'\r\n' + \
            b''.join(
                key + b':' + value + b'\r\n'
                for (key, value) in headers
            ) + \
            b'\r\n'
        await sendall(loop, sock, header)

    async def recv_header(sock):
        unprocessed = b''
        while True:
            unprocessed += await recv(loop, sock, recv_bufsize)
            header_end = unprocessed.index(b'\r\n\r\n')
            if header_end != -1:
                break
        header_bytes, unprocessed = unprocessed[:header_end], unprocessed[header_end + 4:]
        lines = header_bytes.split(b'\r\n')
        code = lines[0][9:12]
        response_headers = tuple(
            (key.strip().lower(), value.strip())
            for line in lines[1:]
            for (key, _, value) in (line.partition(b':'),)
        )
        transfer_encoding = dict(response_headers).get(b'transfer-encoding', b'identity')
        body_handler = transfer_encoding_handler(transfer_encoding)

        return code, response_headers, body_handler, unprocessed

    async def send_body(sock, body):
        async for chunk in body:
            if chunk:
                await sendall(loop, sock, chunk)

    async def response_body_generator(sock, body_handler, response_headers, unprocessed):
        try:
            generator = body_handler(loop, sock, recv_bufsize, response_headers, unprocessed)
            unprocessed = None  # So can be garbage collected

            async for chunk in generator:
                yield chunk
        finally:
            sock.close()

    async def close():
        dns_resolver_clear_cache()

    return request, close


async def identity_handler(loop, sock, recv_bufsize, response_headers, unprocessed):
    total_received = 0
    total_remaining = int(dict(response_headers).get(b'content-length', 0))

    if unprocessed and total_remaining:
        total_received += len(unprocessed)
        total_remaining -= len(unprocessed)
        yield unprocessed

    while total_remaining:
        # Allow the previous data to be garbage collected
        unprocessed = None
        unprocessed = await recv(loop, sock, recv_bufsize)
        total_received += len(unprocessed)
        total_remaining -= len(unprocessed)
        if total_remaining < 0:
            raise IOError()
        yield unprocessed


async def chunked_handler(loop, sock, recv_bufsize, _, unprocessed):
    while True:
        # Fetch until have chunk header
        while b'\r\n' not in unprocessed:
            unprocessed += await recv(loop, sock, recv_bufsize)

        # Find chunk length
        chunk_header_end = unprocessed.index(b'\r\n')
        chunk_header_hex = unprocessed[:chunk_header_end]
        chunk_length = int(chunk_header_hex, 16)

        # End of body signalled by a 0-length chunk
        if chunk_length == 0:
            break

        # Remove chunk header
        unprocessed = unprocessed[chunk_header_end + 2:]

        # Yield whatever amount of chunk we have already, which
        # might be nothing
        chunk_remaining = chunk_length
        in_chunk, unprocessed = \
            unprocessed[:chunk_remaining], unprocessed[chunk_remaining:]
        if in_chunk:
            yield in_chunk
        chunk_remaining -= len(in_chunk)

        # Fetch and yield rest of chunk
        while chunk_remaining:
            unprocessed += await recv(loop, sock, recv_bufsize)
            in_chunk, unprocessed = \
                unprocessed[:chunk_remaining], unprocessed[chunk_remaining:]
            chunk_remaining -= len(in_chunk)
            yield in_chunk

        # Fetch until have chunk footer, and remove
        while len(unprocessed) < 2:
            unprocessed += await recv(loop, sock, recv_bufsize)
        unprocessed = unprocessed[2:]


async def sendall(loop, sock, data):
    try:
        latest_num_bytes = sock.send(data)
    except (BlockingIOError, ssl.SSLWantWriteError):
        latest_num_bytes = 0
    else:
        if latest_num_bytes == 0:
            raise IOError()

    if latest_num_bytes == len(data):
        return

    total_num_bytes = latest_num_bytes

    def writer():
        nonlocal total_num_bytes
        try:
            latest_num_bytes = sock.send(data_memoryview[total_num_bytes:])
        except (BlockingIOError, ssl.SSLWantWriteError):
            pass
        except BaseException as exception:
            if not result.done():
                result.set_exception(exception)
        else:
            total_num_bytes += latest_num_bytes
            if latest_num_bytes == 0 and not result.done():
                result.set_exception(IOError())
            elif total_num_bytes == len(data) and not result.done():
                result.set_result(None)

    result = asyncio.Future()
    fileno = sock.fileno()
    loop.add_writer(fileno, writer)
    data_memoryview = memoryview(data)

    try:
        return await result
    finally:
        loop.remove_writer(fileno)


async def recv(loop, sock, recv_bufsize):
    incoming = await _recv(loop, sock, recv_bufsize)
    if not incoming:
        raise IOError()
    return incoming


async def _recv(loop, sock, recv_bufsize):
    try:
        return sock.recv(recv_bufsize)
    except (BlockingIOError, ssl.SSLWantReadError):
        pass

    def reader():
        try:
            chunk = sock.recv(recv_bufsize)
        except (BlockingIOError, ssl.SSLWantReadError):
            pass
        except BaseException as exception:
            if not result.done():
                result.set_exception(exception)
        else:
            if not result.done():
                result.set_result(chunk)

    result = asyncio.Future()
    fileno = sock.fileno()
    loop.add_reader(fileno, reader)

    try:
        return await result
    finally:
        loop.remove_reader(fileno)


async def tls_complete_handshake(loop, ssl_sock):
    try:
        return ssl_sock.do_handshake()
    except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
        pass

    def handshake():
        try:
            ssl_sock.do_handshake()
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
            pass
        except BaseException as exception:
            if not done.done():
                done.set_exception(exception)
        else:
            if not done.done():
                done.set_result(None)

    done = asyncio.Future()
    fileno = ssl_sock.fileno()
    loop.add_reader(fileno, handshake)
    loop.add_writer(fileno, handshake)

    try:
        return await done
    finally:
        loop.remove_reader(fileno)
        loop.remove_writer(fileno)
