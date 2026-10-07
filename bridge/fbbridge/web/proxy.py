# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Reverse proxy from the web page's /fb/ to fbd, so the Files panel and the file viewer
run inside the web app. fbd accepts only its own Host and Origin (127.0.0.1:<port>); the
proxy sets them for requests the site already found to come from this same page (Origin ==
Host), so fbd's cross-site protection still holds for browsers on the network."""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
from urllib.parse import parse_qs, urlsplit

from .httpd import HttpError

PREFIX = "/fb"
# The only request headers passed on (fbd's Host, Origin, token and length are set below).
PASS = {"accept", "accept-language", "content-type", "user-agent", "if-match", "if-none-match", "range",
        "x-fb-client", "x-fb-host", "sec-websocket-key", "sec-websocket-version"}


# What the web app may ask of fbd: the panel's page, reading, editing and file operations.
# Not: acting on the Mac itself (open, reveal, typing into a terminal, windows, updates,
# enabling hosts, recovery) nor switching web access, which stay with the Mac's own panel.
ALLOWED = {
    "GET": re.compile(r"/(|main\.js|main\.css|chunks/[\w.-]+)|/api/(state|ws|ls|file|raw|workspace|prefs|view/pending)"),
    "PUT": re.compile(r"/api/(file|workspace|prefs)"),
    "POST": re.compile(r"/api/fs/(mkdir|touch|rename|trash)"),
}


def allowed(method, target):
    path, _, query = target.partition("?")
    rule = ALLOWED.get(method)
    if not rule or not rule.fullmatch(path):
        return False
    if path == "/api/workspace" and method == "PUT":   # only the web app's own workspaces
        return all(k.startswith("web:") for k in parse_qs(query).get("key", [""]))
    return True


def without_token(target):
    """The page never holds fbd's token; a `t=` it sends is dropped, the proxy adds its own."""
    path, _, query = target.partition("?")
    kept = "&".join(p for p in query.split("&") if p and not p.startswith("t="))
    return path + ("?" + kept if kept else "")


def same_origin(req):
    origin = req.header("origin")
    return not origin or urlsplit(origin).netloc == req.header("host")


def framable(csp, host):
    """fbd's own policy for its panel page, changed only so this page may frame it and it may
    reach this host. Every other directive, and every other response's policy (such as the
    sandbox on raw files), passes as fbd wrote it."""
    out = []
    for d in csp.split(";"):
        name = d.strip().split(" ", 1)[0]
        if name == "frame-ancestors":
            d = " frame-ancestors 'self'"
        elif name == "connect-src":
            d = f" connect-src 'self' ws://{host} wss://{host}"
        out.append(d)
    return ";".join(out)


_proven = None   # (port, token) fbd proved it knows; None after a connection failed


async def prove(port, token):
    """Before the token goes out, the program on fbd's port proves it knows it (AC-07): while
    fbd is not running another program may hold the port. Asked again after a failure."""
    global _proven
    if _proven == (port, token):
        return
    nonce = secrets.token_hex(16)
    r, w = await asyncio.open_connection("127.0.0.1", port)
    try:
        w.write(f"GET /api/hello?n={nonce} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode())
        answer = await asyncio.wait_for(r.read(4096), 5)
    finally:
        w.close()
    want = hmac.new(token.encode(), f"fbd-hello-v1:{nonce}".encode(), hashlib.sha256).hexdigest()
    body = answer.partition(b"\r\n\r\n")[2]
    try:
        ok = hmac.compare_digest(json.loads(body).get("proof", ""), want)
    except (ValueError, AttributeError):
        ok = False
    if not ok:
        raise HttpError(502, "The program on iterm-enhancer's port is not fbd; the token was not sent.")
    _proven = (port, token)


async def forward(req, fbd_port, token):
    """Send req (under /fb/, its origin checked by the site) to fbd with fbd's token and copy
    its answer back, the panel's event socket included."""
    global _proven
    target = without_token(req.target[len(PREFIX):] or "/")
    if not allowed(req.method, target):
        raise HttpError(403, "not available in the web app")
    asked = req.wants_websocket()
    if asked and target.partition("?")[0] != "/api/ws":
        raise HttpError(400, "only the panel's event socket is a WebSocket")
    fbd = f"127.0.0.1:{fbd_port}"
    try:
        await prove(fbd_port, token)
        up_r, up_w = await asyncio.open_connection("127.0.0.1", fbd_port)
    except (OSError, asyncio.IncompleteReadError, TimeoutError):
        _proven = None
        raise HttpError(502, "The iterm-enhancer backend (fbd) is not running.")
    headers = {k: v for k, v in req.headers.items() if k in PASS}
    headers["host"] = fbd
    headers["x-fb-token"] = token
    if asked or req.header("origin"):
        headers["origin"] = f"http://{fbd}"
    if asked:
        headers["connection"], headers["upgrade"] = "Upgrade", "websocket"
    else:
        headers["connection"] = "close"
        if req.body:
            headers["content-length"] = str(len(req.body))
    head = f"{req.method} {target} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    up_w.writelines((head.encode("latin-1"), req.body))          # no copy of a large file
    await up_w.drain()
    try:
        status_head = await up_r.readuntil(b"\r\n\r\n")
        accepted = asked and status_head.startswith(b"HTTP/1.1 101")   # a refusal is an ordinary answer
        req.writer.write(rewrite_head(status_head, req.header("host"), keep_alive=accepted))
        await req.writer.drain()
        if accepted:
            await asyncio.gather(pump(req.reader, up_w), pump(up_r, req.writer))
        elif asked:                          # refused: its body by length, as fbd keeps the connection
            m = re.search(rb"(?im)^content-length:\s*(\d+)", status_head)
            req.writer.write(await up_r.readexactly(int(m.group(1))) if m else b"")
        else:
            await pump(up_r, req.writer)
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        up_w.close()


def rewrite_head(raw, host, keep_alive):
    lines = [line for line in raw.decode("latin-1").split("\r\n") if line]
    html = any(line.lower().startswith("content-type:") and "text/html" in line.lower() for line in lines)
    out = [lines[0]]
    for line in lines[1:]:
        name, _, value = line.partition(":")
        name = name.strip().lower()
        if name == "connection" and not keep_alive:
            continue
        if name == "content-security-policy" and html:
            line = f"Content-Security-Policy: {framable(value.strip(), host)}"
        out.append(line)
    if not keep_alive:
        out.append("Connection: close")
    return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


async def pump(src, dst):
    try:
        while chunk := await src.read(65536):
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass
