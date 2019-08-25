import asyncio
import hashlib
import itertools
import json
import unittest
import ssl

from aiofastforward import (
    FastForward,
)
from aiohttp import (
    web,
)

from lowhaio import (
    HttpDataError,
    HttpHeaderTooLong,
    HttpTlsError,
    Pool,
    buffered,
    streamed,
    timeout,
)


def async_test(func):
    def wrapper(*args, **kwargs):
        future = func(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)
    return wrapper


class TestIntegration(unittest.TestCase):

    def add_async_cleanup(self, coroutine, *args):
        loop = asyncio.get_event_loop()
        self.addCleanup(loop.run_until_complete, coroutine(*args))

    @async_test
    async def test_http_post(self):
        posted_data_received = b''

        async def handle_get(request):
            nonlocal posted_data_received
            posted_data_received = await request.content.read()
            return web.Response(status=200)

        app = web.Application()
        app.add_routes([
            web.post('/page', handle_get)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        async def data():
            for _ in range(10):
                yield b'abc' * 1000000

        content_length = str(1000000 * 10 * 3).encode()

        request, close = Pool()
        self.add_async_cleanup(close)
        _, _, body = await request(
            b'POST', 'http://localhost:8080/page', (), (
                (b'content-length', content_length),
            ), data,
        )
        async for _ in body:
            pass
        self.assertEqual(posted_data_received, b'abc' * 1000000 * 10)

    @async_test
    async def test_http_large_header(self):
        posted_data_received = b''

        async def handle_get(request):
            nonlocal posted_data_received
            posted_data_received = await request.content.read()
            return web.Response(status=200, headers={'example': 'b' * 32000})

        app = web.Application()
        app.add_routes([
            web.get('/page', handle_get)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)

        with self.assertRaises(HttpHeaderTooLong):
            await request(b'GET', 'http://localhost:8080/page')

    @async_test
    async def test_http_delete_200_no_content_length_no_body_keep_alive(self):

        async def handle_delete(request):
            await request.content.read()
            response = web.StreamResponse(status=200, headers={'connection': 'keep-alive'})
            await response.prepare(request)
            return web.Response(status=200)

        app = web.Application()
        app.add_routes([
            web.delete('/page', handle_delete)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool(http_version=b'HTTP/1.0')
        self.add_async_cleanup(close)
        code, headers, body = await request(
            b'DELETE', 'http://localhost:8080/page'
        )
        self.assertEqual(code, b'200')
        self.assertNotIn('content-length', dict(headers))
        self.assertNotIn('transfer-encoding', dict(headers))
        self.assertEqual(b'keep-alive', dict(headers)[b'connection'])
        body_bytes = await buffered(body)
        self.assertEqual(body_bytes, b'')

    @async_test
    async def test_http_delete_200_with_body(self):

        async def handle_delete(request):
            await request.content.read()
            return web.Response(status=200, body=b'some-data')

        app = web.Application()
        app.add_routes([
            web.delete('/page', handle_delete)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)
        code, _, body = await request(
            b'DELETE', 'http://localhost:8080/page'
        )
        self.assertEqual(code, b'200')
        body_bytes = await buffered(body)
        self.assertEqual(body_bytes, b'some-data')

    @async_test
    async def test_http_head_200(self):

        async def handle_head(request):
            await request.content.read()
            return web.Response(status=200, headers={'content-length': '20'})

        app = web.Application()
        app.add_routes([
            web.head('/page', handle_head)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)
        code, _, body = await request(
            b'HEAD', 'http://localhost:8080/page'
        )
        await buffered(body)
        self.assertEqual(code, b'200')

    @async_test
    async def test_http_head_200_chunked(self):

        async def handle_head(request):
            await request.content.read()
            return web.Response(status=200, headers={'transfer-encoding': 'chunked'})

        app = web.Application()
        app.add_routes([
            web.head('/page', handle_head)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)
        code, _, body = await request(
            b'HEAD', 'http://localhost:8080/page'
        )
        await buffered(body)
        self.assertEqual(code, b'200')

    @async_test
    async def test_http_post_204_without_content(self):

        async def handle_post(request):
            await request.content.read()
            return web.Response(status=204)

        app = web.Application()
        app.add_routes([
            web.post('/page', handle_post)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)
        code, _, body = await request(
            b'POST', 'http://localhost:8080/page'
        )
        self.assertEqual(code, b'204')
        body_bytes = await buffered(body)
        self.assertEqual(body_bytes, b'')

    @async_test
    async def test_http_post_204_with_content(self):
        # Against the spec, but that's the server's fault: we still need to deal with it

        async def handle_post(request):
            await request.content.read()
            response = web.StreamResponse(status=204, headers={'content-length': '12'})
            await response.prepare(request)
            await response.write(b'some-content')
            return response

        app = web.Application()
        app.add_routes([
            web.post('/page', handle_post)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)
        code, _, body = await request(
            b'POST', 'http://localhost:8080/page'
        )
        self.assertEqual(code, b'204')
        body_bytes = await buffered(body)
        self.assertEqual(body_bytes, b'some-content')

    @async_test
    async def test_http_chunked_responses(self):
        response_datas = []

        data = b'abcdefghijklmnopqrstuvwxyz'
        chunk_size = None

        async def handle_get(request):
            await request.content.read()
            response = web.StreamResponse()
            await response.prepare(request)

            for chars in [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]:
                await response.write(chars)
                await asyncio.sleep(0)

            return response

        app = web.Application()
        app.add_routes([
            web.get('/page', handle_get)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)

        for recv_bufsize, keep_alive_timeout in itertools.product((1, 26, 16384), (0, 15)):
            request, close = Pool(recv_bufsize=recv_bufsize, keep_alive_timeout=keep_alive_timeout)
            self.add_async_cleanup(close)
            for chunk_size in range(1, 27):
                _, headers, body = await request(
                    b'GET', 'http://localhost:8080/page',
                )
                self.assertEqual(dict(headers)[b'transfer-encoding'], b'chunked')
                response_data = b''
                async for body_bytes in body:
                    response_data += body_bytes
                response_datas.append(response_data)

        self.assertEqual(response_datas, [data] * 26 * 3 * 2)

    @async_test
    async def test_http_identity_responses(self):
        response_datas = []

        data = b'abcdefghijklmnopqrstuvwxyz'
        chunk_size = None

        async def handle_get(request):
            await request.content.read()
            response = web.StreamResponse(headers={'content-length': '26'})
            await response.prepare(request)

            for chars in [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]:
                await response.write(chars)
                await asyncio.sleep(0)

            return response

        app = web.Application()
        app.add_routes([
            web.get('/page', handle_get)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        for recv_bufsize, keep_alive_timeout in itertools.product((1, 26, 16384), (0, 15)):
            request, close = Pool(recv_bufsize=recv_bufsize, keep_alive_timeout=keep_alive_timeout)
            self.add_async_cleanup(close)

            for chunk_size in range(1, 27):
                _, _, body = await request(
                    b'GET', 'http://localhost:8080/page',
                )
                response_data = b''
                async for body_bytes in body:
                    response_data += body_bytes
                response_datas.append(response_data)

        self.assertEqual(response_datas, [data] * 26 * 3 * 2)

    @async_test
    async def test_http_identity_close_no_content_length_responses(self):
        response_datas = []

        data = b'abcdefghijklmnopqrstuvwxyz'
        chunk_size = None

        async def handle_get(request):
            await request.content.read()
            response = web.StreamResponse()
            response.force_close()
            await response.prepare(request)

            for chars in [data[i:i+chunk_size] for i in range(0, len(data), chunk_size)]:
                await response.write(chars)
                await asyncio.sleep(0)

            return response

        app = web.Application()
        app.add_routes([
            web.get('/page', handle_get)
        ])
        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        combinations = itertools.product((1, 26, 16384), (b'close', b'keep-alive'), (0, 15))
        for recv_bufsize, connection, keep_alive_timeout in combinations:
            request, close = Pool(recv_bufsize=recv_bufsize, keep_alive_timeout=keep_alive_timeout,
                                  http_version=b'HTTP/1.0')
            self.add_async_cleanup(close)

            for chunk_size in range(1, 27):
                _, _, body = await request(
                    b'GET', 'http://localhost:8080/page', headers=((b'connection', connection),),
                )
                response_data = b''
                async for body_bytes in body:
                    response_data += body_bytes
                response_datas.append(response_data)

        self.assertEqual(response_datas, [data] * 26 * 3 * 2 * 2)

    @async_test
    async def test_ssl_self_signed_fails_by_default(self):
        loop = asyncio.get_event_loop()

        def handler(_, __):
            pass
        loop.set_exception_handler(handler)

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        ssl_context.load_cert_chain('public.crt', keyfile='private.key')
        app = web.Application()

        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080, ssl_context=ssl_context)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)
        with self.assertRaises(HttpTlsError) as cm:
            await request(b'GET', 'https://localhost:8080/page')

        self.assertIsInstance(cm.exception.__cause__, ssl.SSLError)

    @async_test
    async def test_after_keep_alive_timeout(self):
        loop = asyncio.get_event_loop()
        app = web.Application()

        bodies = iter([b'abcd', b'efgh', b'ijkl'])
        remote_ports = []

        async def handle_get(request):
            remote_ports.append(request.transport._sock.getpeername()[1])
            return web.Response(status=200, body=next(bodies))

        app.add_routes([
            web.get('/page', handle_get)
        ])

        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        with FastForward(loop) as forward:

            request, close = Pool()
            self.add_async_cleanup(close)

            _, _, body_1_stream = await request(b'GET', 'http://localhost:8080/page')
            body_1 = await buffered(body_1_stream)

            await forward(15)

            _, _, body_2_stream = await request(b'GET', 'http://localhost:8080/page')
            body_2 = await buffered(body_2_stream)

            _, _, body_3_stream = await request(b'GET', 'http://localhost:8080/page')
            body_3 = await buffered(body_3_stream)

        self.assertEqual(body_1, b'abcd')
        self.assertEqual(body_2, b'efgh')
        self.assertEqual(body_3, b'ijkl')

        self.assertNotEqual(remote_ports[0], remote_ports[1])
        self.assertEqual(remote_ports[1], remote_ports[2])

    @async_test
    async def test_http_timeout(self):
        loop = asyncio.get_event_loop()
        app = web.Application()

        async def handle_get(_):
            await asyncio.Future()

        app.add_routes([
            web.get('/page', handle_get)
        ])

        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        with FastForward(loop) as forward:

            request, close = Pool()
            self.add_async_cleanup(close)

            request = asyncio.ensure_future(request(b'GET', 'http://localhost:8080/page'))
            with self.assertRaises(HttpDataError) as cm:
                at_10 = forward(10)
                await request

            await at_10
        self.assertIsInstance(cm.exception.__cause__, asyncio.TimeoutError)

    @async_test
    async def test_cancel_raised_cancelled_error(self):
        app = web.Application()

        request_made = asyncio.Event()

        async def handle_get(_):
            request_made.set()
            await asyncio.Future()

        app.add_routes([
            web.get('/page', handle_get)
        ])

        runner = web.AppRunner(app)
        await runner.setup()
        self.add_async_cleanup(runner.cleanup)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

        request, close = Pool()
        self.add_async_cleanup(close)

        request = asyncio.ensure_future(request(b'GET', 'http://localhost:8080/page'))
        await request_made.wait()
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request


class TestEndToEnd(unittest.TestCase):

    def add_async_cleanup(self, coroutine, *args):
        loop = asyncio.get_event_loop()
        self.addCleanup(loop.run_until_complete, coroutine(*args))

    @async_test
    async def test_http_post_small(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        async def data():
            yield b'some-data=something'

        code, headers, body = await request(
            b'POST', 'http://postman-echo.com/post', (), (
                (b'content-length', b'19'),
                (b'content-type', b'application/x-www-form-urlencoded'),
            ), data,
        )
        body_bytes = b''
        async for chunk in body:
            body_bytes += chunk

        headers_dict = dict(headers)
        response_dict = json.loads(body_bytes)

        self.assertEqual(code, b'200')
        self.assertEqual(headers_dict[b'content-type'], b'application/json; charset=utf-8')
        self.assertEqual(response_dict['headers']['host'], 'postman-echo.com')
        self.assertEqual(response_dict['headers']['content-length'], '19')
        self.assertEqual(response_dict['form'], {'some-data': 'something'})

    @async_test
    async def test_http_post_small_buffered_streamed(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        code, headers, body = await request(
            b'POST', 'http://postman-echo.com/post', (), (
                (b'content-length', b'19'),
                (b'content-type', b'application/x-www-form-urlencoded'),
            ), streamed(b'some-data=something'),
        )
        body_bytes = await buffered(body)

        headers_dict = dict(headers)
        response_dict = json.loads(body_bytes)

        self.assertEqual(code, b'200')
        self.assertEqual(headers_dict[b'content-type'], b'application/json; charset=utf-8')
        self.assertEqual(response_dict['headers']['host'], 'postman-echo.com')
        self.assertEqual(response_dict['headers']['content-length'], '19')
        self.assertEqual(response_dict['form'], {'some-data': 'something'})

    @async_test
    async def test_https_get_chunked(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        _, _, body = await request(
            b'GET', 'https://postman-echo.com/stream/1000',
            params=(('some', 'arg'),),
        )
        body_bytes = await buffered(body)

        # Slightly odd response: a concatanation of identical JSON objects
        part_length = int(len(body_bytes) / 1000)
        parts = [body_bytes[i:i+part_length] for i in range(0, len(body_bytes), part_length)]

        self.assertEqual(len(parts), 1000)

        for part in parts:
            response_dict = json.loads(part)
            self.assertEqual(response_dict['headers']['host'], 'postman-echo.com')
            self.assertEqual(response_dict['args'], {'n': '1000', 'some': 'arg'})

    @async_test
    async def test_http_get_chunked(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        _, _, body = await request(
            b'GET', 'http://postman-echo.com/stream/1000',
            params=(('some', 'arg'),),
        )
        body_bytes = await buffered(body)

        # Slightly odd response: a concatanation of identical JSON objects
        part_length = int(len(body_bytes) / 1000)
        parts = [body_bytes[i:i+part_length] for i in range(0, len(body_bytes), part_length)]

        self.assertEqual(len(parts), 1000)

        for part in parts:
            response_dict = json.loads(part)
            self.assertEqual(response_dict['headers']['host'], 'postman-echo.com')
            self.assertEqual(response_dict['args'], {'n': '1000', 'some': 'arg'})

    @async_test
    async def test_https_post_small(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        async def data():
            yield b'some-data=something'

        code, headers, body = await request(
            b'POST', 'https://postman-echo.com/post',
            params=(
                ('some', 'value'),
                ('?=&', '/&'),
            ),
            headers=(
                (b'content-length', b'19'),
                (b'content-type', b'application/x-www-form-urlencoded'),
            ),
            body=data,
        )
        body_bytes = b''
        async for chunk in body:
            body_bytes += chunk

        headers_dict = dict(headers)
        response_dict = json.loads(body_bytes)

        self.assertEqual(code, b'200')
        self.assertEqual(headers_dict[b'content-type'], b'application/json; charset=utf-8')
        self.assertEqual(response_dict['args'], {'some': 'value', '?=&': '/&'})
        self.assertEqual(response_dict['headers']['host'], 'postman-echo.com')
        self.assertEqual(response_dict['headers']['content-length'], '19')
        self.assertEqual(response_dict['form'], {'some-data': 'something'})

    @async_test
    async def test_http_get_small_via_dns(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        _, _, body = await request(b'GET', 'http://www.ovh.net/files/1Mio.dat')
        m = hashlib.md5()
        async for chunk in body:
            m.update(chunk)

        self.assertEqual(m.hexdigest(), '6cb91af4ed4c60c11613b75cd1fc6116')

    @async_test
    async def test_http_get_small_via_ip_address(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        _, _, body = await request(b'GET', 'http://212.183.159.230/5MB.zip')
        m = hashlib.md5()
        async for chunk in body:
            m.update(chunk)

        self.assertEqual(m.hexdigest(), 'b3215c06647bc550406a9c8ccc378756')


class TestTimeout(unittest.TestCase):

    @async_test
    async def test_shorter_than_timeout_not_raises(self):
        loop = asyncio.get_event_loop()

        async def worker():
            with timeout(loop, 1):
                await asyncio.sleep(0.5)

        with FastForward(loop) as forward:
            task = asyncio.ensure_future(worker())

            await forward(0.5)
            await task

    @async_test
    async def test_longer_than_timeout_raises_timeout_error(self):
        loop = asyncio.get_event_loop()

        async def worker():
            with timeout(loop, 1):
                await asyncio.sleep(1.5)

        with FastForward(loop) as forward:
            task = asyncio.ensure_future(worker())

            await forward(1)
            with self.assertRaises(asyncio.TimeoutError):
                await task

    @async_test
    async def test_cancel_raises_cancelled_error(self):
        loop = asyncio.get_event_loop()

        async def worker():
            with timeout(loop, 1):
                await asyncio.sleep(0.5)

        with FastForward(loop) as forward:
            task = asyncio.ensure_future(worker())

            await forward(0.25)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    @async_test
    async def test_exception_propagates(self):
        loop = asyncio.get_event_loop()

        async def worker():
            with timeout(loop, 2):
                raise Exception('inner')

        with FastForward(loop) as forward:
            task = asyncio.ensure_future(worker())

            await forward(1)
            with self.assertRaisesRegex(Exception, 'inner'):
                await task

    @async_test
    async def test_cleanup(self):
        loop = asyncio.get_event_loop()
        cleanup = asyncio.Event()

        async def worker():
            with timeout(loop, 1):
                try:
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    cleanup.set()
                    raise

        with FastForward(loop) as forward:
            task = asyncio.ensure_future(worker())

            await forward(1)
            with self.assertRaises(asyncio.TimeoutError):
                await task

            self.assertTrue(cleanup.is_set())

    @async_test
    async def test_ignore_timeout(self):
        loop = asyncio.get_event_loop()
        ignored = asyncio.Event()

        async def worker():
            with timeout(loop, 1):
                try:
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    # Swallow the exception
                    pass
            ignored.set()

        with FastForward(loop) as forward:
            task = asyncio.ensure_future(worker())

            await forward(1)
            await task
            self.assertTrue(ignored.is_set())
