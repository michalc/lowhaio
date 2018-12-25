from collections import (
    namedtuple,
)
from contextlib import (
    asynccontextmanager,
)
from socket import (
    AF_INET, IPPROTO_TCP, SHUT_RDWR, SOCK_STREAM,
    socket,
)


ConnectionPool = namedtuple('ConnectionPool', ('connection'))
Connection = namedtuple('Connection', ('sock'))


@asynccontextmanager
async def connection_pool(loop):

    @asynccontextmanager
    async def connection(ip_address, port):
        sock = socket(family=AF_INET, type=SOCK_STREAM, proto=IPPROTO_TCP)
        sock.setblocking(False)

        try:
            await loop.sock_connect(sock, (ip_address, port))
            yield Connection(sock)
        finally:
            try:
                sock.shutdown(SHUT_RDWR)
            finally:
                sock.close()

    yield ConnectionPool(connection=connection)
