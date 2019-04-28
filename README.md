# lowhaio [![CircleCI](https://circleci.com/gh/michalc/lowhaio.svg?style=svg)](https://circleci.com/gh/michalc/lowhaio) [![Maintainability](https://api.codeclimate.com/v1/badges/418d72f1de909bff27b6/maintainability)](https://codeclimate.com/github/michalc/lowhaio/maintainability) [![Test Coverage](https://api.codeclimate.com/v1/badges/418d72f1de909bff27b6/test_coverage)](https://codeclimate.com/github/michalc/lowhaio/test_coverage)

---

Work in progress. These docs serve as a rough design spec.

---


A lightweight Python asyncio HTTP/1.1 client. No additional tasks are created; all code is in a single module; and other than the standard library only a single dependency is required, [aiodnsresolver](https://github.com/michalc/aiodnsresolver).


## Usage

The API is streaming-first: for both request and response bodies, asynchronous generators are used.

```python
import os
from lowhaio import Pool

request, _ = Pool()

path = 'my.file'
content_length = str(os.stat(path).st_size).encode()
async def file_data():
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(16384), b''):
            yield chunk

code, headers, body = await request(
    b'POST', 'https://example.com/path',
    params=(), headers=((b'content-length': content_length),), body=file_data(),
)
async for chunk in body:
    print(chunk)
```

However, there are helper functions `streamed` and `buffered` when this isn't required or possible.

```python
from lowhaio import Pool, streamed, buffered

request, _ = Pool()

content = b'some-data'
content_length = b'9'
code, headers, body = await request(
    b'POST', 'https://example.com/path',
    params=(), headers=((b'content-length': content_length),), body=streamed(content),
)

response = await buffered(body)
```


## Scope

The scope of the core functions is restricted to:

- (TLS) connection opening, closing and pooling;
- passing and receiving HTTP headers and streaming bodies;
- decoding chunked responses.

This is to make the core behaviour useful to a reasonable range of uses, but to _not_ include what can be added by layer(s) on top. Specifically not included:

- following redirects (although this is planned for a helper function);
- cookies;
- compressing/decompressing requests/responses;
- authentication.
