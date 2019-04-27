import asyncio
import json
import unittest

from lowhaio import (
    Pool,
)


def async_test(func):
    def wrapper(*args, **kwargs):
        future = func(*args, **kwargs)
        loop = asyncio.get_event_loop()
        loop.run_until_complete(future)
    return wrapper


class TestEndToEnd(unittest.TestCase):

    def add_async_cleanup(self, loop, coroutine, *args):
        self.addCleanup(loop.run_until_complete, coroutine(*args))

    @async_test
    async def test_post_small(self):
        request, _ = Pool()

        async def data():
            yield b'some-data=something'

        code, headers, body = await request(
            b'POST', 'http://postman-echo.com/post', (
                (b'content-length', b'19'),
                (b'content-type', b'application/x-www-form-urlencoded'),
            ), lambda: data,
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
    async def test_get_small(self):
        request, _ = Pool()

        async def data():
            yield b''

        _, _, body = await request(
            b'GET', 'http://speed.hetzner.de/100MB.bin', (), lambda: data,
        )
        total_in = 0
        async for chunk in body:
            total_in += len(chunk)

        self.assertEqual(total_in, 104857600)
