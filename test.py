import asyncio
import hashlib
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

    def add_async_cleanup(self, coroutine, *args):
        loop = asyncio.get_event_loop()
        self.addCleanup(loop.run_until_complete, coroutine(*args))

    @async_test
    async def test_post_small(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        async def data():
            yield b'some-data=something'

        code, headers, body = await request(
            b'POST', 'http://postman-echo.com/post', (
                (b'content-length', b'19'),
                (b'content-type', b'application/x-www-form-urlencoded'),
            ), data(),
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
    async def test_get_small_via_dns(self):
        request, close = Pool()
        self.add_async_cleanup(close)

        async def data():
            yield b''

        _, _, body = await request(
            b'GET', 'http://www.ovh.net/files/1Mio.dat', (), data(),
        )
        m = hashlib.md5()
        async for chunk in body:
            m.update(chunk)

        self.assertEqual(m.hexdigest(), '6cb91af4ed4c60c11613b75cd1fc6116')

    @async_test
    async def test_get_small_via_ip_address(self):
        request, _ = Pool()

        async def data():
            yield b''

        _, _, body = await request(
            b'GET', 'http://212.183.159.230/5MB.zip', (), data(),
        )
        m = hashlib.md5()
        async for chunk in body:
            m.update(chunk)

        self.assertEqual(m.hexdigest(), 'b3215c06647bc550406a9c8ccc378756')
