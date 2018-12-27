from asyncio import (
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
)


ConnectionPool = namedtuple('ConnectionPool', ('connection'))
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
            sock = await ssl_unwrap_socket(loop, ssl_sock) if ssl_sock else sock

        cleanups = [cleanup_ssl_unwrap, cleanup_sock_shutdown, cleanup_sock_close]

        sock = socket(family=AF_INET, type=SOCK_STREAM, proto=IPPROTO_TCP)
        sock.setblocking(False)
        ssl_sock = None

        try:
            await sock_connect(loop, sock, (ip_address, port))

            ssl_sock = ssl_context.wrap_socket(
                sock, server_hostname=hostname, do_handshake_on_connect=False)
            await ssl_handshake(loop, ssl_sock)

            yield Connection(ssl_sock)
        finally:
            for cleanup in cleanups:
                try:
                    await cleanup()
                except BaseException:
                    pass

    yield ConnectionPool(connection=connection)


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

    return await done
