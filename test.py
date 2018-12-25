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

from lowhaio import (
    connection_pool,
)


def async_test(func):
    def wrapper(*args, **kwargs):
        future = func(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)
    return wrapper


async def server(loop, client_handler):

    server_sock = socket(family=AF_INET, type=SOCK_STREAM, proto=IPPROTO_TCP)
    server_sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    server_sock.setblocking(False)
    server_sock.bind(('', 8080))
    server_sock.listen(IPPROTO_TCP)

    listening = asyncio.Future()

    def on_listening(success):
        if listening.done():
            return

        if success:
            listening.set_result(None)
        else:
            listening.set_exception(Exception())

    client_tasks = set()

    def create_client_task(sock):
        # Client task is created immediately after socket is connected, in a
        # sync function and without a yield, so cleanup always happens, even
        # if the server task is immediately cancelled
        task = loop.create_task(client_handler(sock))
        client_tasks.add(task)

        def done(_):
            client_tasks.remove(task)
            try:
                sock.shutdown(SHUT_RDWR)
            except OSError:
                pass
            sock.close()

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
    await listening
    return task, server_sock


async def sock_accept(loop, server_sock, on_listening, create_client_task):
    fileno = server_sock.fileno()
    done = asyncio.Future()

    def accept():
        try:
            sock, _ = server_sock.accept()
        except BlockingIOError:
            pass
        except BaseException as exception:
            on_listening(False)
            if not done.cancelled():
                done.set_exception(exception)
        else:
            sock.setblocking(False)
            create_client_task(sock)
            if not done.cancelled():
                done.set_result(None)

    accept()
    on_listening(True)
    loop.add_reader(fileno, accept)

    try:
        return await done
    finally:
        loop.remove_reader(fileno)


class TestServer(unittest.TestCase):

    @async_test
    async def test_close_after_client_not_raises(self):
        loop = asyncio.get_running_loop()

        server_wait_forever = asyncio.Future()

        async def server_client(_):
            await server_wait_forever

        server_task, _ = await server(loop, server_client)

        async with \
                connection_pool(loop) as pool, \
                pool.connection('127.0.0.1', 8080):
            pass

        server_task.cancel()
        await asyncio.sleep(0)

    @async_test
    async def test_close_by_cancel_before_client_raises(self):
        loop = asyncio.get_running_loop()

        server_wait_forever = asyncio.Future()

        async def server_client(_):
            await server_wait_forever

        server_task, _ = await server(loop, server_client)

        with self.assertRaises(OSError):
            async with \
                    connection_pool(loop) as pool, \
                    pool.connection('127.0.0.1', 8080) as connection:
                server_task.cancel()
                while True:
                    connection.sock.send(b'-')
                    await asyncio.sleep(0)

    @async_test
    async def test_close_by_client_sock_close_before_client_raises(self):
        loop = asyncio.get_running_loop()

        async def server_client(sock):
            sock.close()

        server_task, _ = await server(loop, server_client)

        with self.assertRaises(OSError):
            async with \
                    connection_pool(loop) as pool, \
                    pool.connection('127.0.0.1', 8080) as connection:
                server_task.cancel()
                while True:
                    connection.sock.send(b'-')
                    await asyncio.sleep(0)

    @async_test
    async def test_close_by_server_sock_close_before_client_raises(self):
        loop = asyncio.get_running_loop()

        async def server_client(_):
            pass

        server_task, server_sock = await server(loop, server_client)

        with self.assertRaises(OSError):
            async with connection_pool(loop) as pool:
                async with pool.connection('127.0.0.1', 8080):
                    pass

                server_sock.close()
                await pool.connection('127.0.0.1', 8080).__aenter__()

        server_task.cancel()
        await asyncio.sleep(0)
