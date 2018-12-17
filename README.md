# lowhaio

A lightweight and dependency-free Python asyncio HTTP/1.1 client. Rather than abstracting away the HTTP protocol or aspects of the connection, the provided API is a small suite of 6 utility functions that exposes low-level behaviour. This provides both flexibility and minimisation of unnecessary code and processing in the production application. Clients can of course wrap lowhaio calls in their own functions to reduce duplication or provide abstractions as they see fit.


## Responsibilities of lowhaio

- Connection pooling
- Creation of TLS/SSL connections, i.e. for HTTPS
- Streaming of requests and responses
- Parsing of HTTP headers

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
        lowhaio.pool(max_connections=20, max_sequential_connections=20, max_connection_idle_time=5, socket_available_timeout=20) as pool, \
        lowhaio.socket(pool, host, port, ssl_context) as socket:

    await lowhaio.send(pool, socket, chunk_size=4096, timeout=10,
        b'POST /path HTTP/1.1\r\n'
        b'Host: www.w3.org\r\n'
        b'\r\n'
        b'Body'
    )

    code = await lowhaio.recv_status_line(pool, socket, chunk_size=4096, timeout=10):
    async for header_key, header_value in lowhaio.recv_headers(pool, socket, remainder, chunk_size=4096, timeout=10):
        # Do something with the header values

    async for body_bytes in lowhaio.recv_body(pool, socket, chunk_size=4096, timeout=10):
        # Save the body bytes
```


## Design

- Apart from where necessary to interact with Python APIs, classes are not used.
- Internal data structures are exposed wherever possible to provide flexibility, reduce unnecessary indirection, and to avoid hidden state.
- Separate functions are preferred over boolean-esque options passed to fewer function to avoid unnecessary dynamic behaviour.


## Recipies

### Non-streaming requests and responses

### Sending a dictionary of HTTP headers

### Receiving a dictionary of HTTP headers

### Chunked transfer encoding

### Compressed content encoding
