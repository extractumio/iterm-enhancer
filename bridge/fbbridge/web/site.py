# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The web app's requests (AC-52, AC-53): sign-in, the page, the terminal WebSocket, and the
Files panel through the proxy to fbd. Everything but the page and sign-in needs the cookie."""
import asyncio
import hashlib
import json
import mimetypes
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs

from ..common import APP_DIR, LOCAL_HOST, PORT
from . import httpd, paste, proxy
from .auth import COOKIE, Auth, client_address
from .mirror import CLOSED, Client, Hub
from .net import host_name, lan_addresses

STATIC = Path(__file__).with_name("static")
PASTES = 2                                   # images uploading at once
TOKEN_FILE = APP_DIR / "token"
PAGE_HEADERS = {
    "Cache-Control": "no-cache",            # kept, but checked against its ETag on every load
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def page_files(static=STATIC):
    """The page's own files, read once: (path -> (bytes, type, etag), build). The build names
    all of them together; index.html carries it, so an open page of another build reloads."""
    raw = {"/" + f.relative_to(static).as_posix(): f.read_bytes() for f in sorted(static.rglob("*")) if f.is_file()}
    build = hashlib.sha1(b"".join(p.encode() + b"\0" + d for p, d in raw.items())).hexdigest()[:12]
    raw["/index.html"] = raw["/index.html"].replace(b"__BUILD__", build.encode())
    files = {p: (d, mimetypes.guess_type(p)[0] or "application/octet-stream", '"%s"' % hashlib.sha1(d).hexdigest()[:16])
             for p, d in raw.items()}
    files["/"] = files["/index.html"]
    return files, build


class Site:
    def __init__(self, conn, app, cfg, verify, files_of, addresses, paste_to=None, tmux=None, sessions=None):
        self.conn, self.cfg, self.files_of, self.paste_to = conn, cfg, files_of, paste_to
        # sessions: where sign-ins outlive a restart (config.SESSIONS); none for a test's Site
        self.auth, self.hub = Auth(verify, sessions, (cfg.get("password") or {}).get("salt", "")), Hub(app, tmux)
        self.names = {"localhost", "127.0.0.1", "::1", LOCAL_HOST, f"{LOCAL_HOST}.local", *cfg["allow_hosts"]}
        self.set_addresses(addresses)
        self.files, self.build = page_files()
        # Passwords are checked one at a time, on a thread of their own: a flood of sign-ins can
        # neither run guesses in parallel past the brake nor take the threads the bridge uses.
        self.login_lock = asyncio.Lock()
        self.hasher = ThreadPoolExecutor(1, thread_name_prefix="web-login")
        self._token = (None, "")             # (file mtime, fbd's token): read again only when fbd made a new one
        self.uploads = set()                 # connections sending a pasted image (the body is held in memory)

    def set_addresses(self, addresses):
        self.hosts, self.hosts_at = self.names | set(addresses), time.monotonic()

    def fbd_token(self):
        try:
            mtime = TOKEN_FILE.stat().st_mtime_ns
        except FileNotFoundError:
            raise httpd.HttpError(502, "The iterm-enhancer backend (fbd) has no token yet; is iTerm2 running?")
        if self._token[0] != mtime:
            self._token = (mtime, TOKEN_FILE.read_text().strip())
        return self._token[1]

    async def host_allowed(self, host):
        """A page on another site that points its own DNS name at this Mac (DNS rebinding) sends
        that name as Host; only this Mac's own names and addresses are served. The addresses are
        read again (at most every 10 s) when an unknown one comes: the network may have changed."""
        name = host_name(host)
        if name not in self.hosts and time.monotonic() - self.hosts_at > 10:
            self.set_addresses(await asyncio.get_running_loop().run_in_executor(None, lan_addresses))
        return name in self.hosts or name.endswith(".ts.net")

    def body_limit(self, req):
        """Only a signed-in file save or pasted image may be large; anything else, a sign-in
        included, is small. Images: at most PASTES at once, each held in memory until saved."""
        if req.method in ("PUT", "POST") and req.path.startswith(proxy.PREFIX + "/") and self.signed_in(req):
            return httpd.MAX_BODY
        if req.method == "POST" and req.path in ("/paste", "/upload") and self.signed_in(req):
            self.uploads = {w for w in self.uploads if not w.is_closing()}
            if len(self.uploads) >= PASTES:
                raise httpd.HttpError(429, "Two files are uploading already; try this one again in a moment.")
            self.uploads.add(req.writer)
            return paste.MAX_BYTES
        return httpd.SMALL_BODY

    def signed_in(self, req):
        return self.auth.check_session(req.cookie(COOKIE))

    async def handle(self, req):
        if not await self.host_allowed(req.header("host")):
            raise httpd.HttpError(403, "unknown host")
        path = req.path
        if req.method != "GET" or req.wants_websocket() or path == "/ws":
            # another site open in the same browser must not sign in or out, drive a terminal, or
            # change files (browsers name the page on every such request)
            if not req.header("origin") or not proxy.same_origin(req):
                raise httpd.HttpError(403, "cross-site request")
        secure = req.header("x-forwarded-proto") == "https"
        if path == "/auth/login" and req.method == "POST":
            req.writer.write(await self.login(req, secure))
        elif path == "/auth/logout" and req.method == "POST":
            self.auth.sign_out(req.cookie(COOKIE))
            req.writer.write(httpd.response(204, headers={"Set-Cookie": Auth.cookie_header("", secure)}))
        elif path == "/auth/check":
            req.writer.write(httpd.response(204 if self.signed_in(req) else 401))
        elif path == "/ws":
            if not self.signed_in(req):
                raise httpd.HttpError(401, "not signed in")
            ws = await httpd.WebSocket.accept(req)
            await Client(self.conn, self.hub, ws, self.cfg["history"], self.files_of, self.build).run()
        elif path in ("/paste", "/upload") and req.method == "POST":
            if not self.signed_in(req):
                raise httpd.HttpError(401, "not signed in")
            req.writer.write(await self.paste(req))
        elif path == proxy.PREFIX or path.startswith(proxy.PREFIX + "/"):
            if not self.signed_in(req):
                raise httpd.HttpError(401, "Sign in again: reload the page.")
            await proxy.forward(req, PORT, self.fbd_token())
        elif req.method == "GET":
            req.writer.write(self.static(req))
        else:
            raise httpd.HttpError(405, "method not allowed")

    async def login(self, req, secure):
        """A password for a sign-in cookie. One check at a time, and a wrong one holds the next
        for a second: at most one guess a second in all, then waits per address and in all."""
        try:
            password = str(json.loads(req.body or b"{}").get("password", ""))
        except (ValueError, AttributeError):
            raise httpd.HttpError(400, "bad request")
        address = client_address(req.peer, req.header("x-forwarded-for"))
        async with self.login_lock:
            wait = self.auth.locked_for(address)
            if wait:
                return httpd.json_response(429, {"reason": "locked", "retry": wait})
            token = await asyncio.get_running_loop().run_in_executor(self.hasher, self.auth.check_password, password, address, secure)
            if not token:
                await asyncio.sleep(1)
                return httpd.json_response(401, {"reason": "wrong", "retry": self.auth.locked_for(address)})
        return httpd.response(204, headers={"Set-Cookie": Auth.cookie_header(token, secure)})

    async def paste(self, req):
        """A pasted image (/paste) or an uploaded file of any kind (/upload), saved where the pane's
        shell runs: {"path"}, or {"error"} for a toast."""
        sid = parse_qs(req.target.partition("?")[2]).get("sid", [""])[0]
        session = self.hub.app.get_session_by_id(sid) if sid else None
        if not session:
            return httpd.json_response(404, {"error": CLOSED})
        try:
            query = parse_qs(req.target.partition("?")[2])
            name = (paste.upload_name(query.get("name", [""])[0], req.body) if req.path == "/upload"
                    else paste.checked_name(req.header("content-type"), req.body))
            target = await self.paste_to(session)
            loop = asyncio.get_running_loop()      # disk and ssh off the loop that follows iTerm2
            if target is None:
                path = await loop.run_in_executor(None, paste.save_local, name, req.body)
            else:
                path = await loop.run_in_executor(None, paste.save_remote, target, name, req.body)
        except paste.PasteError as e:
            return httpd.json_response(422, {"error": str(e)})
        except OSError as e:
            return httpd.json_response(500, {"error": f"The image was not saved: {e.strerror or e}"})
        return httpd.json_response(200, {"path": path})

    def static(self, req):
        f = self.files.get(req.path)
        if not f:
            return httpd.text_response(404, "not found")
        data, ctype, etag = f
        if req.header("if-none-match") == etag:
            return httpd.response(304, headers={"ETag": etag, **PAGE_HEADERS})
        return httpd.response(200, data, {"Content-Type": ctype, "ETag": etag, **PAGE_HEADERS})

    async def close(self):
        """Sign everyone out of this server: their pages get the sign-in back, iTerm its sizes."""
        self.hub.stop()
        self.auth.close()
        for c in list(self.hub.clients):
            await c.ws.close()
        self.hasher.shutdown(wait=False)
