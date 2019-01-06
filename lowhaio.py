from asyncio import (
    CancelledError,
    Future,
)
from socket import (
    AF_INET, IPPROTO_TCP, SHUT_RDWR, SOCK_STREAM, SOL_SOCKET, SO_ERROR,
    socket,
)
from ssl import (
    SSLWantReadError,
    SSLWantWriteError,
)


class ConnectionPool:
    pass


class Connection:
    __slots__ = ('sock', 'buf', 'buf_memoryview', 'cursor_received', 'cursor_parsed')

    def __init__(self, sock, buf):
        self.sock = sock
        self.buf = buf
        self.buf_memoryview = memoryview(buf)
        self.cursor_received = 0
        self.cursor_parsed = 0


class AsyncContextManager:
    __slots__ = ('generator', )

    def __init__(self, generator):
        self.generator = generator

    async def __aenter__(self):
        return await self.generator.__anext__()

    async def __aexit__(self, typ, value, traceback):
        try:
            coro = \
                self.generator.__anext__() if typ is None else \
                self.generator.athrow(typ, value, traceback)
            await coro
        except StopAsyncIteration:
            return


def asynccontextmanager(generator_func):

    def wrapped(*args, **kwargs):
        return AsyncContextManager(generator_func(*args, **kwargs))

    return wrapped


@asynccontextmanager
async def connection_pool(_):
    yield ConnectionPool()


@asynccontextmanager
async def connection(loop, hostname, ip_address, port, ssl_context, buf_memoryview):

    async def cleanup_sock_close():
        sock.close()

    async def cleanup_sock_shutdown():
        try:
            sock.shutdown(SHUT_RDWR)
        except BaseException:
            pass

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

        yield Connection(sock, buf_memoryview)
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


async def send(loop, conn, buf_memoryview):
    cursor = 0
    while cursor != len(buf_memoryview):
        num_bytes = await send_at_least_one_byte(loop, conn.sock, buf_memoryview[cursor:])
        cursor += num_bytes


async def recv_code(loop, conn):
    while True:
        conn.cursor_received += await recv_at_least_one_byte(
            loop, conn.sock, conn.buf_memoryview[conn.cursor_received:])
        conn.cursor_parsed = conn.buf.find(b'\r\n', 0, conn.cursor_received)

        if conn.cursor_parsed != -1:
            break
        elif conn.cursor_received == len(conn.buf):
            # The first line did not fit into the buffer
            raise ValueError()

    code = int(conn.buf[9:min(12, conn.cursor_parsed)])
    return code


async def recv_headers(loop, conn):

    while True:
        newline_index_1 = conn.cursor_parsed
        newline_index_2 = conn.buf.find(b'\r\n', conn.cursor_parsed + 2, conn.cursor_received)

        if newline_index_2 == newline_index_1 + 2:
            # End of headers, ready to receive body
            conn.cursor_parsed = newline_index_2 + 2
            return

        if newline_index_2 != -1:
            # Header
            yield conn.buf[conn.cursor_parsed + 2:newline_index_2].split(b':')
            conn.cursor_parsed = newline_index_2

        elif conn.cursor_received == len(conn.buf):
            # Headers don't fit in buffer
            raise ValueError()

        else:
            # No header yet
            num_received = await recv_at_least_one_byte(
                loop, conn.sock, conn.buf_memoryview[conn.cursor_received:])
            conn.cursor_received += num_received

            if not num_received:
                raise ValueError()


async def recv_body(loop, conn):

    while True:
        # If fetching the header resulted in _exactly_ the http header bytes
        # received and no more, we shouldn't yield an empty array, since this
        # is often taken as EOF
        if conn.cursor_parsed != conn.cursor_received:
            yield conn.buf_memoryview[conn.cursor_parsed:conn.cursor_received]

        conn.cursor_parsed = conn.cursor_received

        num_bytes = await recv_at_least_one_byte(loop, conn.sock,
                                                 conn.buf_memoryview[conn.cursor_received:])
        if not num_bytes:
            break

        conn.cursor_received += num_bytes


async def send_at_least_one_byte(loop, sock, buf):
    fileno = sock.fileno()
    done = Future()

    def write():
        try:
            num_bytes = sock.send(buf)
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


async def recv_at_least_one_byte(loop, sock, buf_memoryview):
    fileno = sock.fileno()
    done = Future()

    def read():
        try:
            num_bytes = sock.recv_into(buf_memoryview)
        except (SSLWantReadError, BlockingIOError):
            pass
        except BaseException as exception:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_exception(exception)
        else:
            loop.remove_reader(fileno)
            if not done.done():
                done.set_result(num_bytes)

    loop.add_reader(fileno, read)
    read()

    try:
        return await done
    except CancelledError:
        loop.remove_reader(fileno)
        raise
