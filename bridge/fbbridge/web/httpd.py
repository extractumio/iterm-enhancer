# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""A small HTTP/1.1 server on asyncio streams: requests, responses and a server-side
WebSocket (RFC 6455). Standard library only, so it does not depend on the version of the
`websockets` package that iTerm2's script runtime happens to ship."""
import asyncio
import base64
import hashlib
import json
import re
import struct

from ..common import log

MAX_HEAD = 32 * 1024
MAX_BODY = 64 * 1024 * 1024        # a saved file travels in one request
SMALL_BODY = 4096                  # what any other request may carry (a sign-in, a JSON action)
MAX_MESSAGE = 1 << 20              # a WebSocket message from the browser
HEAD_TIMEOUT = 10                  # seconds for a request head (and its body) to arrive
MAX_CONNECTIONS = 64               # open at once; more are refused, the bridge's descriptors stay free
TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
UNSAFE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")      # control characters, but not tab
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
REASONS = {101: "Switching Protocols", 200: "OK", 204: "No Content", 304: "Not Modified", 400: "Bad Request",
           429: "Too Many Requests",
           401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
           411: "Length Required", 413: "Payload Too Large", 500: "Internal Server Error", 502: "Bad Gateway",
           503: "Service Unavailable"}


class Request:
    def __init__(self, method, target, headers, reader, writer):
        self.method, self.target = method, target
        self.headers = headers                     # lower-case name -> value
        self.reader, self.writer = reader, writer
        self.path = target.partition("?")[0]
        self.body = b""

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    @property
    def peer(self):
        info = self.writer.get_extra_info("peername")
        return info[0] if info else "?"

    def wants_websocket(self):
        return (self.header("upgrade").lower() == "websocket"
                and "upgrade" in self.header("connection").lower())

    def cookie(self, name):
        for part in self.header("cookie").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""


async def read_request(reader, writer, body_limit=lambda req: SMALL_BODY):
    """The next request with its body, or None when the client closed the connection.
    body_limit(req) says how large a body this request may carry (decided from its head)."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    except asyncio.LimitOverrunError:
        raise HttpError(400, "request head too large")
    lines = head.decode("latin-1").split("\r\n")[:-2]
    # nothing that a proxy behind could read differently: no bare LF or other control characters
    if any(UNSAFE.search(line) for line in lines):
        raise HttpError(400, "control characters in the request head")
    try:
        method, target, _version = lines[0].split(" ", 2)
    except ValueError:
        raise HttpError(400, "bad request line")
    headers = {}
    for line in lines[1:]:
        k, sep, v = line.partition(":")
        if not sep or not TOKEN.fullmatch(k):
            raise HttpError(400, "bad header")
        headers[k.lower()] = v.strip()
    req = Request(method, target, headers, reader, writer)
    if "chunked" in req.header("transfer-encoding").lower():
        raise HttpError(411, "chunked request bodies are not supported")
    try:
        length = int(req.header("content-length") or 0)
    except ValueError:
        raise HttpError(400, "bad Content-Length")
    if length < 0 or length > body_limit(req):
        raise HttpError(413, "request body too large")
    if length:
        req.body = await reader.readexactly(length)
    return req


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def text_response(status, message):
    return response(status, message, {"Content-Type": "text/plain; charset=utf-8"})


def json_response(status, value):
    return response(status, json.dumps(value), {"Content-Type": "application/json"})


def response(status, body=b"", headers=None):
    """Bytes of a complete response that closes the connection."""
    if isinstance(body, str):
        body = body.encode()
    h = {"Content-Length": str(len(body)), "Connection": "close", **(headers or {})}
    head = f"HTTP/1.1 {status} {REASONS.get(status, '')}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in h.items())
    return head.encode("latin-1") + b"\r\n" + body


# ---------- WebSocket (server side) ----------

class ConnectionClosed(Exception):
    pass


def unmask(data, mask):
    """XOR with the repeated 4-byte mask, as one integer operation (not a loop per byte)."""
    n = len(data)
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


class WebSocket:
    """Text messages over an upgraded connection; pings are answered, closes are honored."""

    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer
        self.closed = False
        self._lock = asyncio.Lock()

    @staticmethod
    async def accept(req):
        key = req.header("sec-websocket-key")
        if not key or req.header("sec-websocket-version") != "13":
            raise HttpError(400, "not a WebSocket handshake")
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        req.writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await req.writer.drain()
        return WebSocket(req.reader, req.writer)

    async def _frame(self, opcode, payload=b""):
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([n])
        elif n < 1 << 16:
            head += bytes([126]) + struct.pack("!H", n)
        else:
            head += bytes([127]) + struct.pack("!Q", n)
        async with self._lock:
            if self.closed and opcode != 0x8:
                raise ConnectionClosed()
            try:
                self.writer.writelines((head, payload))       # no copy of a large payload
                await self.writer.drain()
            except (ConnectionError, RuntimeError) as e:
                self.closed = True
                raise ConnectionClosed() from e

    async def send(self, text):
        await self._frame(0x1, text.encode())

    async def recv(self):
        """The next text message; raises ConnectionClosed when the peer goes away."""
        parts, opcode = [], None
        while True:
            try:
                b1, b2 = await self.reader.readexactly(2)
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", await self.reader.readexactly(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", await self.reader.readexactly(8))[0]
                if not b2 & 0x80 or n > MAX_MESSAGE or sum(map(len, parts)) + n > MAX_MESSAGE:
                    raise ConnectionClosed()           # clients must mask; and stay small
                mask = await self.reader.readexactly(4)
                data = unmask(await self.reader.readexactly(n), mask)
            except (asyncio.IncompleteReadError, ConnectionError) as e:
                self.closed = True
                raise ConnectionClosed() from e
            op = b1 & 0x0F
            if op == 0x8:
                await self.close()
                raise ConnectionClosed()
            if op == 0x9:
                await self._frame(0xA, data)
                continue
            if op == 0xA:
                continue
            if op in (0x1, 0x2):
                opcode, parts = op, [data]
            elif op == 0x0 and opcode is not None:
                parts.append(data)
            if b1 & 0x80 and opcode is not None:
                return b"".join(parts).decode(errors="replace")

    async def close(self):
        if not self.closed:
            try:
                await self._frame(0x8, struct.pack("!H", 1000))
            except ConnectionClosed:
                pass
            self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.recv()
        except ConnectionClosed:
            raise StopAsyncIteration


class Server:
    """The listener and its open connections, so switching off closes those too."""

    def __init__(self):
        self.listener = None
        self.open = set()

    def close(self):
        self.listener.close()
        for w in list(self.open):
            w.close()


async def serve(handler, host, port, body_limit=lambda req: SMALL_BODY):
    """Run handler(request) for each connection's single request. The handler writes the
    response (or upgrades); the connection is closed afterwards."""
    server = Server()

    async def on_connect(reader, writer):
        if len(server.open) >= MAX_CONNECTIONS:
            writer.write(response(503, "too many connections"))
            writer.close()
            return
        server.open.add(writer)
        try:
            req = await asyncio.wait_for(read_request(reader, writer, body_limit), HEAD_TIMEOUT)
            if req:
                await handler(req)
        except HttpError as e:
            writer.write(text_response(e.status, str(e)))
        except (ConnectionError, asyncio.IncompleteReadError, TimeoutError):
            pass
        except Exception as e:  # fail loud: an answer, and a line in the log
            log(f"web: {type(e).__name__}: {e}")
            writer.write(text_response(500, "internal error"))
        finally:
            server.open.discard(writer)
            try:
                await writer.drain()
                writer.close()
            except Exception:
                pass

    server.listener = await asyncio.start_server(on_connect, host, port, limit=MAX_HEAD)
    return server
