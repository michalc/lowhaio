import asyncio
import unittest
from socket import (
    AF_INET,
    IPPROTO_TCP,
    SHUT_RDWR,
    SO_REUSEADDR,
    SOCK_STREAM,
    SOL_SOCKET,
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
    connection_pool,
    get_connection,
    send,
    recv,
    ssl_handshake,
    ssl_unwrap_socket,
)


def async_test(func):
    def wrapper(*args, **kwargs):
        future = func(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)
    return wrapper


async def server(loop, pre_ssl_client_handler, client_handler):

    ssl_context = SSLContext(PROTOCOL_TLSv1_2)
    ssl_context.load_cert_chain('public.crt', keyfile='private.key')
    server_sock = socket(family=AF_INET, type=SOCK_STREAM, proto=IPPROTO_TCP)
    server_sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    server_sock.setblocking(False)
    server_sock.bind(('', 8080))
    server_sock.listen(IPPROTO_TCP)

    listening = asyncio.Event()

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
                await client_handler(sock)
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
        # Client task is created immediately after socket is connected, in a
        # sync function and without a yield, so cleanup always happens, even
        # if the server task is immediately cancelled

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
            await asyncio.sleep(0)

    task = loop.create_task(server_task())
    await listening.wait()
    return task


async def cancel(task):
    task.cancel()
    await asyncio.sleep(0)


async def sock_accept(loop, server_sock, on_listening, create_client_task):
    fileno = server_sock.fileno()
    done = asyncio.Future()

    def accept():
        try:
            sock, _ = server_sock.accept()
        except BlockingIOError:
            pass
        else:
            sock.setblocking(False)
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


class Test(unittest.TestCase):

    def add_async_cleanup(self, loop, coroutine, *args):
        self.addCleanup(loop.run_until_complete, coroutine(*args))

    @async_test
    async def test_server_close_after_client_not_raises(self):
        loop = asyncio.get_running_loop()

        server_task = await server(loop, null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async with \
                connection_pool(loop), \
                get_connection(loop, 'localhost', '127.0.0.1', 8080,
                               SSLContext(PROTOCOL_TLSv1_2)) as connection:
            connection.sock.send(b'-' * 128)
            await asyncio.sleep(0)

    @async_test
    async def test_server_cancel_then_client_send_raises(self):
        loop = asyncio.get_running_loop()

        server_wait_forever = asyncio.Future()

        async def server_client(_):
            await server_wait_forever

        server_task = await server(loop, null_handler, server_client)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(ConnectionError):
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080,
                                   SSLContext(PROTOCOL_TLSv1_2)) as connection:
                server_task.cancel()
                while True:
                    connection.sock.send(b'-')
                    await asyncio.sleep(0)

    @async_test
    async def test_server_cancel_then_connection_raises(self):
        loop = asyncio.get_running_loop()

        server_task = await server(loop, null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(ConnectionRefusedError):
            async with connection_pool(loop):
                async with get_connection(loop, 'localhost', '127.0.0.1', 8080,
                                          SSLContext(PROTOCOL_TLSv1_2)):
                    pass

                server_task.cancel()
                await asyncio.sleep(0)
                # pylint: disable=no-member
                await get_connection(loop, 'localhost', '127.0.0.1', 8080,
                                     SSLContext(PROTOCOL_TLSv1_2)).__aenter__()

    @async_test
    async def test_bad_context_raises(self):
        loop = asyncio.get_running_loop()

        server_task = await server(loop, null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(SSLCertVerificationError):
            async with connection_pool(loop):
                async with get_connection(loop, 'localhost', '127.0.0.1', 8080,
                                          SSLContext(PROTOCOL_TLSv1_2)):
                    pass

                context = create_default_context()
                # pylint: disable=no-member
                await get_connection(loop, 'localhost', '127.0.0.1', 8080, context).__aenter__()

    @async_test
    async def test_bad_ssl_handshake_raises(self):
        loop = asyncio.get_running_loop()

        async def broken_pre_ssl_handler(sock):
            sock.send(b'-')

        server_task = await server(loop, broken_pre_ssl_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(SSLError):
            async with connection_pool(loop):
                # pylint: disable=no-member
                await get_connection(loop, 'localhost', '127.0.0.1', 8080,
                                     SSLContext(PROTOCOL_TLSv1_2)).__aenter__()

    @async_test
    async def test_bad_close_raises(self):
        loop = asyncio.get_running_loop()

        closed = asyncio.Event()

        async def early_close(ssl_sock):
            ssl_sock.close()
            closed.set()

        server_task = await server(loop, null_handler, early_close)
        self.add_async_cleanup(loop, cancel, server_task)

        with self.assertRaises(OSError):
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080,
                                   SSLContext(PROTOCOL_TLSv1_2)) as connection:
                await closed.wait()
                connection.sock.send(b'-' * 128)

    @async_test
    async def test_send_small(self):
        loop = asyncio.get_running_loop()

        done = asyncio.Event()

        data_to_send = b'abcd' * 100
        chunks_received = []
        bytes_received = 0

        async def recv_handler(sock):
            nonlocal bytes_received
            async for chunk in recv(loop, sock, memoryview(bytearray(1024))):
                chunks_received.append(bytes(chunk))
                bytes_received += len(chunk)
                if bytes_received >= len(data_to_send):
                    break
            done.set()

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)
        async with \
                connection_pool(loop), \
                get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
            await send(loop, connection.sock, memoryview(data_to_send), 1)
            await done.wait()

        self.assertEqual(b''.join(chunks_received), data_to_send)

    @async_test
    async def test_send_large(self):
        loop = asyncio.get_running_loop()

        done = asyncio.Event()
        # Large amount of data is required to cause SSLWantWriteError
        data_to_send = b'abcd' * 2097152
        chunks_received = []
        bytes_received = 0

        async def recv_handler(sock):
            nonlocal bytes_received
            async for chunk in recv(loop, sock, memoryview(bytearray(1024))):
                chunks_received.append(bytes(chunk))
                bytes_received += len(chunk)
                if bytes_received >= len(data_to_send):
                    break
            done.set()

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)
        async with \
                connection_pool(loop), \
                get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
            await send(loop, connection.sock, memoryview(data_to_send), 2097152)
            await done.wait()

        self.assertEqual(b''.join(chunks_received), data_to_send)
        await cancel(server_task)

    @async_test
    async def test_send_after_close_raises(self):
        loop = asyncio.get_running_loop()

        done = asyncio.Event()

        async def recv_handler(sock):
            sock.close()
            done.set()

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)
        context = SSLContext(PROTOCOL_TLSv1_2)

        with self.assertRaises(BrokenPipeError):
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
                await done.wait()
                await send(loop, connection.sock, memoryview(bytearray(b'-')), 1)

    @async_test
    async def test_close_after_blocked_send_raises(self):
        loop = asyncio.get_running_loop()

        # Large amount of data is required to cause SSLWantWriteError
        data_to_send = b'abcd' * 2097152

        async def recv_handler(sock):
            async for _ in recv(loop, sock, memoryview(bytearray(1024))):
                break

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)
        with self.assertRaises(OSError):
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
                await send(loop, connection.sock, memoryview(data_to_send), 2097152)

    @async_test
    async def test_send_cancel_propagates(self):
        loop = asyncio.get_running_loop()

        data_to_send = b'abcd' * 2097152
        sending = asyncio.Event()
        server_forever = asyncio.Event()

        async def recv_handler(_):
            await server_forever.wait()

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)

        async def client_recv():
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
                sending.set()
                await send(loop, connection.sock, memoryview(data_to_send), 2097152)

        client_done = asyncio.Event()

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
    async def test_recv_small(self):
        loop = asyncio.get_running_loop()

        data_to_recv = b'abcd' * 100

        async def recv_handler(sock):
            await send(loop, sock, data_to_recv, 1024)

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)

        chunks = []
        async with \
                connection_pool(loop), \
                get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
            async for chunk in recv(loop, connection.sock, bytearray(1)):
                chunks.append(bytes(chunk))

        self.assertEqual(b''.join(chunks), data_to_recv)

    @async_test
    async def test_recv_large(self):
        loop = asyncio.get_running_loop()

        data_to_recv = b'abcd' * 65536

        async def recv_handler(sock):
            await send(loop, sock, data_to_recv, 1024)

        server_task = await server(loop, null_handler, recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)

        chunks = []
        async with \
                connection_pool(loop), \
                get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
            async for chunk in recv(loop, connection.sock, bytearray(131072)):
                chunks.append(bytes(chunk))

        self.assertEqual(b''.join(chunks), data_to_recv)

    @async_test
    async def test_recv_into_bad_array_raises(self):
        loop = asyncio.get_running_loop()

        server_task = await server(loop, null_handler, null_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        context = SSLContext(PROTOCOL_TLSv1_2)

        with self.assertRaises(TypeError):
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
                # pylint: disable=no-member
                await recv(loop, connection.sock, list()).__anext__()

    @async_test
    async def test_recv_cancel_propagates(self):
        loop = asyncio.get_running_loop()

        server_forever = asyncio.Event()
        received_byte = asyncio.Event()

        async def server_recv_handler(sock):
            await send(loop, sock, b'-', 1024)
            await server_forever.wait()

        server_task = await server(loop, null_handler, server_recv_handler)
        self.add_async_cleanup(loop, cancel, server_task)

        async def client_recv():
            context = SSLContext(PROTOCOL_TLSv1_2)
            async with \
                    connection_pool(loop), \
                    get_connection(loop, 'localhost', '127.0.0.1', 8080, context) as connection:
                async for _ in recv(loop, connection.sock, bytearray(1)):
                    received_byte.set()

        client_done = asyncio.Event()

        def set_client_done(_):
            client_done.set()
        client_task = loop.create_task(client_recv())
        client_task.add_done_callback(set_client_done)

        await received_byte.wait()
        client_task.cancel()
        server_task.cancel()
        await client_done.wait()

        self.assertEqual(client_task.cancelled(), True)
