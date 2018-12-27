from asyncio import (
    CancelledError,
    Future,
)
from collections import (
    namedtuple,
)
from contextlib import (
    asynccontextmanager,
)
from socket import (
    AF_INET, IPPROTO_TCP, SHUT_RDWR, SOCK_STREAM, SOL_SOCKET, SO_ERROR,
    socket,
)
from ssl import (
    SSLWantReadError,
    SSLWantWriteError,
)


ConnectionPool = namedtuple('ConnectionPool', ('connection', 'send', 'recv'))
Connection = namedtuple('Connection', ('sock'))


@asynccontextmanager
async def connection_pool(loop):

    @asynccontextmanager
    async def connection(hostname, ip_address, port, ssl_context):

        async def cleanup_sock_close():
            sock.close()

        async def cleanup_sock_shutdown():
            sock.shutdown(SHUT_RDWR)

        async def cleanup_ssl_unwrap():
            nonlocal sock
            sock = await ssl_unwrap_socket(loop, sock)

        cleanups = []
        exceptions = []

        sock = socket(family=AF_INET, type=SOCK_STREAM, proto=IPPROTO_TCP)
        sock.setblocking(False)
        cleanups.append(cleanup_sock_close)

        try:
            cleanups.append(cleanup_sock_shutdown)
            await sock_connect(loop, sock, (ip_address, port))

            sock = ssl_context.wrap_socket(sock, server_hostname=hostname,
                                           do_handshake_on_connect=False)
            cleanups.append(cleanup_ssl_unwrap)
            await ssl_handshake(loop, sock)

            yield Connection(sock)
        except BaseException as exception:
            exceptions.append(exception)
        finally:
            for cleanup in reversed(cleanups):
                try:
                    await cleanup()
                except BaseException as exception:
                    exceptions.append(exception)

        if exceptions:
            raise exceptions[0]

    async def send(connection, buf, chunk_bytes):
        await send_all(loop, connection.sock, buf, chunk_bytes)

    async def recv(connection, buf_memoryview):
        async for chunk in recv_until_close(loop, connection.sock, buf_memoryview):
            yield chunk

    yield ConnectionPool(connection=connection, send=send, recv=recv)


async def sock_connect(loop, sock, address):
    fileno = sock.fileno()
    done = Future()

    def connect():
        try:
            err = sock.getsockopt(SOL_SOCKET, SO_ERROR)
            if err != 0:
                raise OSError(err, f'Connect call failed {address}')
        except BaseException as exception:
            loop.remove_writer(fileno)
            if not done.done():
                done.set_exception(exception)
        else:
            loop.remove_writer(fileno)
            if not done.done():
                done.set_result(None)

    loop.add_writer(fileno, connect)
    try:
        sock.connect(address)
    except BlockingIOError:
        pass

    return await done


async def ssl_handshake(loop, ssl_sock):
    fileno = ssl_sock.fileno()
    done = Future()

    def handshake():
        try:
            ssl_sock.do_handshake()
        except SSLWantReadError:
            pass
        except BaseException as exception:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_exception(exception)
        else:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_result(None)

    loop.add_reader(fileno, handshake)
    handshake()

    return await done


async def ssl_unwrap_socket(loop, ssl_sock):
    fileno = ssl_sock.fileno()
    done = Future()

    def unwrap():
        try:
            sock = ssl_sock.unwrap()
        except SSLWantReadError:
            pass
        except BaseException as exception:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_exception(exception)
        else:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_result(sock)

    loop.add_reader(fileno, unwrap)
    unwrap()

    try:
        return await done
    except CancelledError:
        loop.remove_reader(fileno)
        raise


async def send_all(loop, sock, buf, chunk_bytes):
    cursor = 0
    while cursor != len(buf):
        num_bytes = await send_at_least_one_byte(loop, sock, buf[cursor:], chunk_bytes)
        cursor += num_bytes


async def recv_until_close(loop, sock, buf_memoryview):
    try:
        while True:
            num_bytes = await recv_at_least_one_byte(loop, sock, buf_memoryview,
                                                     len(buf_memoryview))
            yield bytearray(buf_memoryview[:num_bytes])
    except BrokenPipeError:
        pass


async def send_at_least_one_byte(loop, sock, buf, chunk_bytes):
    fileno = sock.fileno()
    max_bytes = min(chunk_bytes, len(buf))
    done = Future()

    def write():
        try:
            num_bytes = sock.send(buf[:max_bytes])
        except (SSLWantWriteError, BlockingIOError):
            pass
        except BaseException as exception:
            loop.remove_writer(fileno)
            if not done.done():
                done.set_exception(exception)
        else:
            loop.remove_writer(fileno)
            if not done.done():
                done.set_result(num_bytes)

    loop.add_writer(fileno, write)
    write()

    try:
        return await done
    except CancelledError:
        loop.remove_writer(fileno)
        raise


async def recv_at_least_one_byte(loop, sock, buf_memoryview, chunk_bytes):
    fileno = sock.fileno()
    max_bytes = min(chunk_bytes, len(buf_memoryview))
    done = Future()

    def read():
        try:
            num_bytes = sock.recv_into(buf_memoryview, max_bytes)
        except (SSLWantReadError, BlockingIOError):
            pass
        except BaseException as exception:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_exception(exception)
        else:
            loop.remove_reader(fileno)
            if not done.done() and num_bytes == 0:
                done.set_exception(BrokenPipeError())
            elif not done.done():
                done.set_result(num_bytes)

    loop.add_reader(fileno, read)
    read()

    try:
        return await done
    except CancelledError:
        loop.remove_reader(fileno)
        raise
