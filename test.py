from asyncio import (
    Event,
    Future,
    get_event_loop,
    sleep,
)
from unittest import (
    TestCase,
)
from socket import (
    AF_INET, IPPROTO_TCP, SHUT_RDWR, SO_REUSEADDR, SOCK_STREAM, SOL_SOCKET,
    socket,
)
from ssl import (
    PROTOCOL_TLSv1_2,
    SSLCertVerificationError,
    SSLContext,
    SSLError,
    create_default_context,
)

from lowhaio import (
    connection,
    connection_pool,
    recv_at_least_one_byte,
    recv_body,
    recv_code,
    recv_headers,
    send,
    ssl_handshake,
    ssl_unwrap_socket,
    Connection,
)


def async_test(func):
    def wrapper(*args, **kwargs):
        future = func(*args, **kwargs)
        loop = get_event_loop()
        loop.run_until_complete(future)
    return wrapper


def ssl_context_server():
    ssl_context = SSLContext(PROTOCOL_TLSv1_2)
    ssl_context.load_cert_chain('public.crt', keyfile='private.key')
    return ssl_context


def ssl_context_client():
    return SSLContext(PROTOCOL_TLSv1_2)


async def server(loop, ssl_context, pre_ssl_client_handler, client_handler):
    server_sock = socket(family=AF_INET, type=SOCK_STREAM, proto=IPPROTO_TCP)
    server_sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    server_sock.setblocking(False)
    server_sock.bind(('', 8080))
    server_sock.listen(IPPROTO_TCP)

    listening = Event()

    def on_listening():
        listening.set()

    client_tasks = set()

    async def client_task(sock):
        await pre_ssl_client_handler(sock)

        try:
            try:
                sock = ssl_context.wrap_socket(
                    sock, server_side=True, do_handshake_on_connect=False)
                await ssl_handshake(loop, sock)
                await client_handler(Connection(sock, memoryview(bytearray(1024))))
            finally:
                try:
                    sock = await ssl_unwrap_socket(loop, sock)
                finally:
                    try:
                        sock.shutdown(SHUT_RDWR)
                    finally:
                        sock.close()
        except BaseException:
            pass

    def create_client_task(sock):
        task = loop.create_task(client_task(sock))
        client_tasks.add(task)

        def done(_):
            client_tasks.remove(task)

        task.add_done_callback(done)

    async def server_task():
        try:
            while server_sock.fileno() != -1:
                await sock_accept(loop, server_sock, on_listening, create_client_task)
        finally:
            server_sock.close()
            for task in client_tasks:
                task.cancel()
            await sleep(0)

    task = loop.create_task(server_task())
    await listening.wait()
    return task


async def cancel(task):
    task.cancel()
    await sleep(0)


async def sock_accept(loop, server_sock, on_listening, create_client_task):
    fileno = server_sock.fileno()
    done = Future()

    def accept():
        try:
            sock, _ = server_sock.accept()
        except BlockingIOError:
            pass
        else:
            sock.setblocking(False)
            # No yield between socket accepted and creating task so always cleaned up
            create_client_task(sock)
            if not done.cancelled():
                done.set_result(None)

    accept()
    on_listening()
    loop.add_reader(fileno, accept)

    try:
        return await done
    finally:
        loop.remove_reader(fileno)


async def null_handler(_):
    pass


async def recv(loop, conn):
    num_bytes = 1
    while num_bytes:
        num_bytes = await recv_at_least_one_byte(loop, conn.sock, conn.buf_memoryview)
        yield conn.buf_memoryview[:num_bytes]


class Test(TestCase):

    def add_async_cleanup(self, loop, coroutine, *args):
        self.addCleanup(loop.run_until_complete, coroutine(*args))

    @async_test
    async def test_server_close_after_client_not_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        server_task = await server(loop, ssl_context_server(), null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
            conn.sock.send(b'-' * 128)
            await sleep(0)

    @async_test
    async def test_server_cancel_then_client_send_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        server_wait_forever = Future()

        async def server_client(_):
            await server_wait_forever

        server_task = await server(loop, ssl_context_server(), null_handler, server_client)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(ConnectionError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
                server_task.cancel()
                while True:
                    conn.sock.send(b'-')
                    await sleep(0)

    @async_test
    async def test_server_cancel_then_connection_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        server_task = await server(loop, ssl_context_server(), null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(ConnectionRefusedError):
            async with connection_pool(loop):
                async with connection(loop, 'localhost', '127.0.0.1', 8080, context, buf):
                    pass

                server_task.cancel()
                await sleep(0)
                # pylint: disable=no-member
                await connection(loop, 'localhost', '127.0.0.1', 8080, context, buf).__aenter__()

    @async_test
    async def test_incompatible_context_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        context_incompatible = create_default_context()
        buf = bytearray()

        server_task = await server(loop, ssl_context_server(), null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(SSLCertVerificationError):
            async with connection_pool(loop):
                async with connection(loop, 'localhost', '127.0.0.1', 8080,
                                      context, buf):
                    pass

                # pylint: disable=no-member
                await connection(loop, 'localhost', '127.0.0.1', 8080,
                                 context_incompatible, buf).__aenter__()

    @async_test
    async def test_bad_ssl_handshake_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        async def broken_pre_ssl(sock):
            sock.send(b'-')

        server_task = await server(loop, ssl_context_server(), broken_pre_ssl, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(SSLError):
            async with connection_pool(loop):
                # pylint: disable=no-member
                await connection(loop, 'localhost', '127.0.0.1', 8080, context, buf).__aenter__()

    @async_test
    async def test_bad_close_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()

        closed = Event()
        buf = bytearray()

        async def early_close(conn):
            conn.sock.close()
            closed.set()

        server_task = await server(loop, ssl_context_server(), null_handler, early_close)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(OSError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
                await closed.wait()
                conn.sock.send(b'-' * 128)

    @async_test
    async def test_send_small(self):
        loop = get_event_loop()
        context = ssl_context_client()

        done = Event()
        buf = bytearray(2)
        data_to_send = b'ab'
        chunks_received = []
        bytes_received = 0

        async def recv_handler(conn):
            nonlocal bytes_received
            async for chunk in recv(loop, conn):
                chunks_received.append(bytes(chunk))
                bytes_received += len(chunk)
                if bytes_received >= len(data_to_send):
                    break
            done.set()

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
            await send(loop, conn, memoryview(data_to_send))
            await done.wait()

        self.assertEqual(b''.join(chunks_received), data_to_send)

    @async_test
    async def test_send_large(self):
        loop = get_event_loop()
        context = ssl_context_client()

        done = Event()
        buf = bytearray(1)
        # Large amount of data is required to cause SSLWantWriteError
        data_to_send = b'abcd' * 2097152
        chunks_received = []
        bytes_received = 0

        async def recv_handler(conn):
            nonlocal bytes_received
            async for chunk in recv(loop, conn):
                chunks_received.append(bytes(chunk))
                bytes_received += len(chunk)
                if bytes_received >= len(data_to_send):
                    break
            done.set()

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
            await send(loop, conn, memoryview(data_to_send))
            await done.wait()

        self.assertEqual(b''.join(chunks_received), data_to_send)
        await cancel(server_task)

    @async_test
    async def test_send_after_close_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        done = Event()

        async def recv_handler(conn):
            conn.sock.close()
            done.set()

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(BrokenPipeError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
                await done.wait()
                await send(loop, conn, memoryview(bytearray(b'-')))

    @async_test
    async def test_close_after_blocked_send_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        # Large amount of data is required to cause SSLWantWriteError
        data_to_send = b'abcd' * 2097152

        async def recv_handler(conn):
            async for _ in recv(loop, conn):
                break

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(OSError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
                await send(loop, conn, memoryview(data_to_send))

    @async_test
    async def test_send_cancel_propagates(self):
        loop = get_event_loop()
        context = ssl_context_client()
        buf = bytearray()

        data_to_send = b'abcd' * 2097152
        sending = Event()
        server_forever = Event()

        async def recv_handler(_):
            await server_forever.wait()

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async def client_recv():
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080,
                               context, buf) as conn:
                sending.set()
                await send(loop, conn, memoryview(data_to_send))

        client_done = Event()

        def set_client_done(_):
            client_done.set()
        client_task = loop.create_task(client_recv())
        client_task.add_done_callback(set_client_done)

        await sending.wait()
        client_task.cancel()
        server_task.cancel()
        await client_done.wait()

        self.assertEqual(client_task.cancelled(), True)

    @async_test
    async def test_recv_small_status_line_too_big(self):
        loop = get_event_loop()
        context = ssl_context_client()

        data_to_recv = \
            b'HTTP/1.1 200 ' + b'OK' * 1024 + b'\r\n' + \
            b'\r\n'

        async def recv_handler(conn):
            await send(loop, conn, data_to_recv)

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(ValueError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080,
                               context, bytearray(1024)) as conn:

                await recv_code(loop, conn)

    @async_test
    async def test_recv_large_status_line_too_big(self):
        loop = get_event_loop()
        context = ssl_context_client()

        data_to_recv = \
            b'HTTP/1.1 200 ' + b'OK' * 1024 + b'\r\n' + \
            b'\r\n'

        async def recv_handler(conn):
            for byte_to_send in data_to_recv:
                await send(loop, conn, bytearray([byte_to_send]))

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(ValueError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080,
                               context, bytearray(1024)) as conn:

                await recv_code(loop, conn)

    @async_test
    async def test_recv_small_no_headers(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            for byte_to_send in data_to_recv:
                await send(loop, conn, bytearray([byte_to_send]))

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        chunks = []
        no_headers = False
        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, bytearray(1024)) as conn:

            code = await recv_code(loop, conn)
            try:
                # pylint: disable=no-member
                await recv_headers(loop, conn).__anext__()
            except StopAsyncIteration:
                no_headers = True
            async for chunk in recv_body(loop, conn):
                chunks.append(bytes(chunk))

        self.assertEqual(code, 200)
        self.assertEqual(no_headers, True)
        self.assertEqual(b''.join(chunks), body_to_recv)

    @async_test
    async def test_recv_large_no_headers(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            await send(loop, conn, data_to_recv)

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        chunks = []
        no_headers = False
        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, bytearray(1024)) as conn:

            code = await recv_code(loop, conn)
            try:
                # pylint: disable=no-member
                await recv_headers(loop, conn).__anext__()
            except StopAsyncIteration:
                no_headers = True
            async for chunk in recv_body(loop, conn):
                chunks.append(bytes(chunk))

        self.assertEqual(code, 200)
        self.assertEqual(no_headers, True)
        self.assertEqual(b''.join(chunks), body_to_recv)

    @async_test
    async def test_recv_small_with_header(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'My:header\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            for byte_to_send in data_to_recv:
                await send(loop, conn, bytearray([byte_to_send]))

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        chunks = []
        headers = []
        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, bytearray(1024)) as conn:

            code = await recv_code(loop, conn)
            async for header_key, header_value in recv_headers(loop, conn):
                headers.append((header_key, header_value))
            async for chunk in recv_body(loop, conn):
                chunks.append(bytes(chunk))

        self.assertEqual(code, 200)
        self.assertEqual(headers, [(b'My', b'header')])
        self.assertEqual(b''.join(chunks), body_to_recv)

    @async_test
    async def test_recv_large_with_header(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'My:header\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            await send(loop, conn, data_to_recv)

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        chunks = []
        headers = []
        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, bytearray(1024)) as conn:

            code = await recv_code(loop, conn)
            async for header_key, header_value in recv_headers(loop, conn):
                headers.append((header_key, header_value))
            async for chunk in recv_body(loop, conn):
                chunks.append(bytes(chunk))

        self.assertEqual(code, 200)
        self.assertEqual(headers, [(b'My', b'header')])
        self.assertEqual(b''.join(chunks), body_to_recv)

    @async_test
    async def test_recv_small_with_headers(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'My:header\r\n' + \
            b'Another:header\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            for byte_to_send in data_to_recv:
                await send(loop, conn, bytearray([byte_to_send]))

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        chunks = []
        headers = []
        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, bytearray(1024)) as conn:

            code = await recv_code(loop, conn)
            async for header_key, header_value in recv_headers(loop, conn):
                headers.append((header_key, header_value))
            async for chunk in recv_body(loop, conn):
                chunks.append(bytes(chunk))

        self.assertEqual(code, 200)
        self.assertEqual(headers, [(b'My', b'header'), (b'Another', b'header')])
        self.assertEqual(b''.join(chunks), body_to_recv)

    @async_test
    async def test_recv_large_with_headers(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'My:header\r\n' + \
            b'Another:header\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            await send(loop, conn, data_to_recv)

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        chunks = []
        headers = []
        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, bytearray(1024)) as conn:

            code = await recv_code(loop, conn)
            async for header_key, header_value in recv_headers(loop, conn):
                headers.append((header_key, header_value))
            async for chunk in recv_body(loop, conn):
                chunks.append(bytes(chunk))

        self.assertEqual(code, 200)
        self.assertEqual(headers, [(b'My', b'header'), (b'Another', b'header')])
        self.assertEqual(b''.join(chunks), body_to_recv)

    @async_test
    async def test_recv_small_header_too_big(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'My:header\r\n' + \
            b'a' * 1024 + b'\r\n' + \
            b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            for byte_to_send in data_to_recv:
                await send(loop, conn, bytearray([byte_to_send]))

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        headers = []
        with self.assertRaises(ValueError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080,
                               context, bytearray(1024)) as conn:

                code = await recv_code(loop, conn)
                async for header_key, header_value in recv_headers(loop, conn):
                    headers.append((header_key, header_value))

        self.assertEqual(code, 200)

    @async_test
    async def test_recv_large_header_too_big(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'My:header\r\n' + \
            b'a' * 1024 + b'\r\n' + \
            body_to_recv

        async def recv_handler(conn):
            await send(loop, conn, data_to_recv)

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        headers = []
        with self.assertRaises(ValueError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080,
                               context, bytearray(1024)) as conn:

                code = await recv_code(loop, conn)
                async for header_key, header_value in recv_headers(loop, conn):
                    headers.append((header_key, header_value))

        self.assertEqual(code, 200)

    @async_test
    async def test_recv_unfinished_header_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()

        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'Some:header\r\n' + \
            b'My:header\r'

        async def recv_handler(conn):
            await send(loop, conn, data_to_recv)

        server_task = await server(loop, ssl_context_server(), null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        headers = []
        with self.assertRaises(ValueError):
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080,
                               context, bytearray(1024)) as conn:

                code = await recv_code(loop, conn)
                async for header_key, header_value in recv_headers(loop, conn):
                    headers.append((header_key, header_value))

        self.assertEqual(code, 200)

    @async_test
    async def test_bad_array_raises(self):
        loop = get_event_loop()
        context = ssl_context_client()

        server_task = await server(loop, ssl_context_server(), null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async with connection_pool(loop):
            with self.assertRaises(TypeError):
                # pylint: disable=no-member
                bad = list()
                await connection(loop, 'localhost', '127.0.0.1', 8080, context, bad).__aenter__()

    @async_test
    async def test_recv_cancel_propagates(self):
        loop = get_event_loop()
        context = ssl_context_client()

        body_to_recv = b'abcd' * 100
        data_to_recv = \
            b'HTTP/1.1 200 OK\r\n' + \
            b'Some:header\r\n' + \
            b'\r\n' + \
            body_to_recv

        buf = bytearray(1024)
        server_forever = Event()
        received_byte = Event()

        async def server_recv_handler(conn):
            await send(loop, conn, data_to_recv)
            await server_forever.wait()

        server_task = await server(loop, ssl_context_server(), null_handler, server_recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        headers = []

        async def client_recv():
            async with \
                    connection_pool(loop), \
                    connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
                _ = await recv_code(loop, conn)
                async for header_key, header_value in recv_headers(loop, conn):
                    headers.append((header_key, header_value))
                async for _ in recv_body(loop, conn):
                    received_byte.set()

        client_done = Event()

        def set_client_done(_):
            client_done.set()
        client_task = loop.create_task(client_recv())
        client_task.add_done_callback(set_client_done)

        await received_byte.wait()
        client_task.cancel()
        server_task.cancel()
        await client_done.wait()

        self.assertEqual(headers, [(b'Some', b'header')])
        self.assertEqual(client_task.cancelled(), True)

    @async_test
    async def test_send_non_ssl(self):
        loop = get_event_loop()
        buf = bytearray(1)

        class NonSSLContext():
            # pylint: disable=no-self-use
            def wrap_socket(self, sock, *_, **__):
                sock.__class__ = NonSSLSocket
                return sock

        class NonSSLSocket(socket):
            __slots__ = ()

            def do_handshake(self):
                pass

            def unwrap(self):
                self.__class__ = socket
                return self

        done = Event()

        data_to_send = b'abcd' * 100
        chunks_received = []
        bytes_received = 0

        async def recv_handler(conn):
            nonlocal bytes_received
            async for chunk in recv(loop, conn):
                chunks_received.append(bytes(chunk))
                bytes_received += len(chunk)
                if bytes_received >= len(data_to_send):
                    break
            done.set()

        context = NonSSLContext()
        context_server = NonSSLContext()

        server_task = await server(loop, context_server, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async with \
                connection_pool(loop), \
                connection(loop, 'localhost', '127.0.0.1', 8080, context, buf) as conn:
            await send(loop, conn, memoryview(data_to_send))
            await done.wait()

        self.assertEqual(b''.join(chunks_received), data_to_send)
