# lowhaio [![CircleCI](https://circleci.com/gh/michalc/lowhaio.svg?style=svg)](https://circleci.com/gh/michalc/lowhaio) [![Maintainability](https://api.codeclimate.com/v1/badges/418d72f1de909bff27b6/maintainability)](https://codeclimate.com/github/michalc/lowhaio/maintainability) [![Test Coverage](https://api.codeclimate.com/v1/badges/418d72f1de909bff27b6/test_coverage)](https://codeclimate.com/github/michalc/lowhaio/test_coverage)

A lightweight and dependency-free Python asyncio HTTP/1.1 client. Rather than abstracting away the HTTP protocol or aspects of the connection, the provided API is a small suite of 6 utility functions that exposes low-level behaviour. This provides both flexibility and minimisation of unnecessary code and processing in the production application. Clients can of course wrap lowhaio calls in their own functions to reduce duplication or provide abstractions as they see fit.

lowhaio is HTTPS-first: non-encrypted HTTP connections are possible, but require a bit more client code.


## Responsibilities of lowhaio

- Connection pooling
- Creation of TLS/SSL connections, i.e. for HTTPS
- Streaming of requests and responses
- Parsing of HTTP headers, and closing connections on "Connection: close" headers.

While many uses do not need requests or responses streamed, it's not possible to provide a meaningful streaming API on top of a non-streaming API, so this is deliberately provided out-of-the-box by lowhaio.


## Responsibilities of client code

- DNS resolution: this is a simple call to `loop.getaddrinfo` (or alternative such as aiodns)
- Parsing URLs: HTTP requests are expressed in terms of protocol, hostname, port and path, and often there is no need to concatanate these together only to then require the concatanation to be parsed.
- Conversion of strings to bytes or vice-versa: many usages do not need encoding or decoding behaviour beyond ASCII.
- Conversion of tuples of bytes to more complex data structures, such as dictionaries.
- Setting connection limits or timeouts: no defaults are appropriate to all situations.
- Serializing HTTP headers: this is trivial.
- Retrying failed connections, including those caused by [the server closing perisistant connections](https://www.w3.org/Protocols/rfc2616/rfc2616-sec8.html#sec8.1.4)
- Calling lowhaio functions in the appropriate order. Some knowledge of the HTTP protocol is required.


## Usage

```python
host = b'www.example.com'
port = 443
ip_address = await loop.getaddrinfo(host, port)
async with \
        lowhaio.connection_pool(max=20, max_wait_connection_seconds=20, max_requests_per_connection=20, max_idle_seconds=5, max_wait_send_recv_seconds=10, chunk_bytes=4096) as pool, \
        pool.connection(ip_address, port, ssl_context) as connection:

    await pool.send(connection,
        b'POST /path HTTP/1.1\r\n'
        b'Host: www.w3.org\r\n'
        b'\r\n'
        b'Body'
    )

    code, cursor = await pool.recv_status_line(connection):
    async for header_key, header_value, cursor in pool.recv_headers(connection, cursor):
        # Do something with the header values

    async for body_bytes in pool.recv_body(connection, cursor):
        # Save the body bytes
```


## Design

Two main principles inform the design of lowhaio.

- Overal understanding of what the final application is doing is a primary concern, and this covers _both_ lowhaio client code and lowhaio internal code. Making an elegant API is _not_ significantly prioritised over a overal understanding what the application is doing in terms of data transformations or bytes sent/received. For example, the API has changed during development because it meant the internal code ended up with unnecessary data transformations/movement.

- All code in the client is required for all uses of the client: nothing is unnecessary. HTTP and HTTPS require different code paths, and so HTTPS is chosen to be supported out of the box over unencrypted HTTP.


## Recipies

### Non-streaming requests and responses

### Sending a dictionary of HTTP headers

### Receiving a dictionary of HTTP headers

### Chunked transfer encoding

### Compressed content encoding

### Non-encrypted connections

```python
class NonSSLContext():
    def wrap_socket(self, socket):
        socket.__class__ = NonSSLSocket

class NonSSLSocket(socket.socket):
    def unwrap_socket(self):
        socket.__class__ = socket.socket

async with lowhaio.socket(pool, host, port, NonSSLContext(),...):
    ...
```
