import asyncio
import urllib.parse
import socket

from aiodnsresolver import (
    TYPES,
    Resolver,
)


def Pool():

    loop = \
        asyncio.get_running_loop() if hasattr(asyncio, 'get_running_loop') else \
        asyncio.get_event_loop()
    resolve, _ = Resolver()

    async def request(method, url, headers, body):
        parsed_url = urllib.parse.urlsplit(url)
        ip_address = str((await resolve(parsed_url.hostname, TYPES.A))[0])
        sock = socket.socket(family=socket.AF_INET, type=socket.SOCK_STREAM,
                             proto=socket.IPPROTO_TCP)
        sock.setblocking(False)

        try:
            await loop.sock_connect(sock, (ip_address, 80))

            outgoing_header = \
                method + b' ' + parsed_url.path.encode() + b' HTTP/1.1\r\n' + \
                b'host:' + parsed_url.hostname.encode() + b'\r\n' + \
                b''.join(
                    key + b':' + value + b'\r\n'
                    for (key, value) in headers
                ) + \
                b'\r\n'
            await loop.sock_sendall(sock, outgoing_header)

            async for chunk in body:
                await loop.sock_sendall(sock, chunk)

            unprocessed = b''
            while True:
                incoming = await loop.sock_recv(sock, 65536)
                if not incoming:
                    raise Exception()
                unprocessed += incoming
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
            response_headers_dict = dict(response_headers)
            content_length = int(response_headers_dict[b'content-length'])

        except BaseException:
            sock.close()
            raise

        async def response_body():
            total_received = 0
            total_remaining = content_length

            try:
                if unprocessed and total_remaining:
                    yield unprocessed
                    total_remaining -= len(unprocessed)

                while total_remaining:
                    max_chunk_length = min(65536, total_remaining)
                    chunk = await loop.sock_recv(sock, max_chunk_length)
                    total_received += len(chunk)
                    total_remaining -= len(chunk)
                    if not chunk:
                        break
                    yield chunk
            finally:
                sock.close()

        return code, response_headers, response_body()

    async def close():
        pass

    return request, close
