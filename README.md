# lowhaio [![CircleCI](https://circleci.com/gh/michalc/lowhaio.svg?style=svg)](https://circleci.com/gh/michalc/lowhaio) [![Test Coverage](https://api.codeclimate.com/v1/badges/418d72f1de909bff27b6/test_coverage)](https://codeclimate.com/github/michalc/lowhaio/test_coverage)

A lightweight Python asyncio HTTP/1.1 client. No additional tasks are created; all code is in a single module; and other than the standard library only a single dependency is required, [aiodnsresolver](https://github.com/michalc/aiodnsresolver)

Lowhaio has a deliberately limited scope: it includes just enough code to be a useful HTTP client and allow more complex behaviour to be added on top if required.

Connections are DNS-aware, in that they are only re-used if they match a current A record for the domain.


## Installation

```bash
pip install lowhaio
```


## Usage

The API is streaming-first: for both request and response bodies, asynchronous iterators are used.

```python
import asyncio
from lowhaio import Pool

async def main():
    request, close = Pool()

    async def request_body():
        yield b'a'
        yield b'bc'

    code, headers, response_body = await request(
        b'POST', 'https://postman-echo.com/post',
        headers=((b'content-length', b'3'), (b'content-type', b'text/plain'),),
        body=request_body,
    )
    async for chunk in response_body:
        print(chunk)

    await close()

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
```

However, there are helper functions `streamed` and `buffered` when this isn't required or possible.

```python
import asyncio
from lowhaio import Pool, streamed, buffered

async def main():
    request, close = Pool()

    request_body = streamed(b'abc')

    code, headers, response_body = await request(
        b'POST', 'https://postman-echo.com/post',
        headers=((b'content-length', b'3'), (b'content-type', b'text/plain'),),
        body=request_body,
    )
    print(await buffered(response_body))

    await close()

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
```


## Headers

The only header automatically added to requests is the `host` header, which is the idna/punycode-encoded domain name from the requested URL.


## Exceptions

Exceptions raised are subclasses of `HttpError`. If a lower-level exception caused this, it is set in the `__cause__` attribute of the `HttpError`

Exceptions before any data is sent are instances of `HttpConnectionError`, and after data is sent, `HttpDataError`. This is to make it possible to know if non-idempotent requests can be retried.


## Custom SSL context

Lowhaio can be used with an custom SSL context through through the `get_ssl_context` parameter to `Pool`. For example, to use the certifi CA bundle, you can install it by

```bash
pip install certifi
```

and use it as below.

```python
import asyncio
import ssl

import certifi
from lowhaio import Pool, buffered, streamed

async def main():
    request, close = Pool(
        get_ssl_context=lambda: ssl.create_default_context(cafile=certifi.where()),
    )

    request_body = streamed(b'abc')

    code, headers, response_body = await request(
        b'POST', 'https://postman-echo.com/post',
        headers=((b'content-length', b'3'), (b'content-type', b'text/plain'),),
        body=request_body,
    )
    print(await buffered(response_body))

    await close()

loop = asyncio.get_event_loop()
loop.run_until_complete(main())
```


## Scope

The scope of the core functions is restricted to:

- (TLS) connection opening, closing and pooling;
- passing and receiving HTTP headers and streaming bodies;
- decoding chunked responses;
- raising exceptions on timeouts.

This is to make the core behaviour useful to a reasonable range of uses, but to _not_ include what can be added by layer(s) on top. Specifically not included:

- following redirects, implemented by [lowhaio-redirect](https://github.com/michalc/lowhaio-redirect);
- retrying failed requests, implemented by [lowhaio-retry](https://github.com/michalc/lowhaio-retry);
- encoding chunked requests, implemented by [lowhaio-chunked](https://github.com/michalc/lowhaio-chunked);
- authentication, such as AWS Signature Version 4 implemented by [lowhaio-aws-sigv4](https://github.com/michalc/lowhaio-aws-sigv4), or AWS Signature Version 4 with unsigned payload implemented by [lowhaio-aws-sigv4-unsigned-payload](https://github.com/michalc/lowhaio-aws-sigv4-unsigned-payload);
- compressing/decompressing requests/responses;
- cookies.
