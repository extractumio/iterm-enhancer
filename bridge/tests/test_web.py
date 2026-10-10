# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52 / AC-53: web access without iTerm2 — the password store, sign-in and its brake, the
HTTP and WebSocket handling, the proxy's rules to fbd, and the labels and lines the browser gets."""
import asyncio
import http.client
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web import auth, config, httpd, layout, net, proxy, screen  # noqa: E402
from fbbridge.web.site import Site  # noqa: E402


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = Path(self.dir.name) / "web.json"

    def tearDown(self):
        self.dir.cleanup()

    def test_the_password_is_kept_as_a_salted_hash_only(self):
        stored = config.hash_password("correct horse")
        config.update(self.file, password=stored, enabled=True)
        text = self.file.read_text()
        self.assertNotIn("correct horse", text)
        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)
        verify = config.verifier(config.load(self.file)["password"])
        self.assertTrue(verify("correct horse"))
        self.assertFalse(verify("correct hors"))
        self.assertNotEqual(stored["salt"], config.hash_password("correct horse")["salt"])

    def test_short_passwords_and_broken_files_are_refused(self):
        with self.assertRaises(ValueError):
            config.hash_password("short")
        self.file.write_text("{not json")
        with self.assertRaises(ValueError):
            config.load(self.file)
        self.assertFalse(config.verifier(None)("anything"))

    def test_defaults_fill_what_is_not_set(self):
        self.assertEqual(config.load(self.file)["port"], 8765)
        self.assertFalse(config.load(self.file)["enabled"])


class AuthTest(unittest.TestCase):
    def test_wrong_passwords_slow_down_one_address_only(self):
        a = auth.Auth(lambda pw: pw == "right one")
        for _ in range(auth.FREE_ATTEMPTS):
            self.assertIsNone(a.check_password("guess", "10.0.0.9"))
        self.assertGreater(a.locked_for("10.0.0.9"), 0)
        self.assertEqual(a.locked_for("10.0.0.5"), 0, "one address cannot lock out another")
        token = a.check_password("right one", "10.0.0.5")
        self.assertTrue(a.check_session(token))
        a.sign_out(token)
        self.assertFalse(a.check_session(token))

    def test_a_proxy_on_this_mac_names_the_browser_others_cannot(self):
        # the proxy appends the address it saw; what the client sent before it is not believed
        self.assertEqual(auth.client_address("127.0.0.1", "6.6.6.6, 100.64.0.7"), "100.64.0.7")
        self.assertEqual(auth.client_address("10.0.0.9", "1.2.3.4"), "10.0.0.9")

    def test_changing_address_does_not_escape_the_brake(self):
        a = auth.Auth(lambda pw: False)
        for i in range(auth.FREE_ATTEMPTS_ALL):
            a.check_password("guess", f"2001:db8::{i}")
        self.assertGreater(a.locked_for("2001:db8::ffff"), 0, "a fresh address waits too")

    def test_the_cookie_is_out_of_scripts_and_other_sites_reach(self):
        c = auth.Auth.cookie_header("tok", secure=True)
        for part in ("itw=tok", "HttpOnly", "SameSite=Strict", "Secure", "Path=/"):
            self.assertIn(part, c)
        self.assertIn("Max-Age=0", auth.Auth.cookie_header("", secure=False))
        self.assertIn(f"Max-Age={7 * 86400}", auth.Auth.cookie_header("t", secure=False), "shorter over plain HTTP")


def run(coro):
    return asyncio.run(coro)


async def parse(raw):
    reader = asyncio.StreamReader(limit=httpd.MAX_HEAD)
    reader.feed_data(raw)
    reader.feed_eof()
    return await httpd.read_request(reader, None)


def frame(text, mask=b"\x01\x02\x03\x04", opcode=0x1):
    data = text.encode()
    return bytes([0x80 | opcode, 0x80 | len(data)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(data))


class HttpTest(unittest.TestCase):
    def test_a_request_with_its_body(self):
        req = run(parse(b"PUT /fb/api/file?path=/a HTTP/1.1\r\nHost: x\r\nContent-Length: 4\r\nCookie: a=1; itw=t\r\n\r\nbody"))
        self.assertEqual((req.method, req.path, req.body, req.cookie("itw")), ("PUT", "/fb/api/file", b"body", "t"))

    def test_bare_line_feeds_and_bad_names_are_refused(self):
        for raw in (b"GET / HTTP/1.1\r\nX: a\nInjected: b\r\n\r\n", b"GET / HTTP/1.1\r\nBad Name: v\r\n\r\n"):
            with self.assertRaises(httpd.HttpError):
                run(parse(raw))

    def test_bodies_are_small_unless_the_caller_allows_more(self):
        with self.assertRaises(httpd.HttpError) as e:
            run(parse(b"POST /auth/login HTTP/1.1\r\nContent-Length: 5000\r\n\r\n" + b"x" * 5000))
        self.assertEqual(e.exception.status, 413)

    def test_chunked_bodies_are_refused(self):
        with self.assertRaises(httpd.HttpError) as e:
            run(parse(b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"))
        self.assertEqual(e.exception.status, 411)

    def test_unmasking_matches_the_byte_by_byte_rule(self):
        data, mask = os.urandom(1001), os.urandom(4)
        self.assertEqual(httpd.unmask(data, mask), bytes(c ^ mask[i % 4] for i, c in enumerate(data)))

    def test_websocket_messages_pings_and_unmasked_frames(self):
        async def go():
            reader = asyncio.StreamReader()
            sent = []
            writer = types.SimpleNamespace(write=sent.append, writelines=lambda parts: sent.append(b"".join(parts)),
                                           drain=lambda: asyncio.sleep(0), close=lambda: None)
            ws = httpd.WebSocket(reader, writer)
            reader.feed_data(frame("ping!", opcode=0x9) + frame("hello"))
            self.assertEqual(await ws.recv(), "hello")
            self.assertEqual(sent[0][0], 0x8A, "a ping is answered with a pong")
            await ws.send("hi")
            self.assertEqual(sent[-1], b"\x81\x02hi")
            reader.feed_data(b"\x81\x05hello")            # clients must mask
            with self.assertRaises(httpd.ConnectionClosed):
                await ws.recv()
        run(go())


class ProxyTest(unittest.TestCase):
    def test_only_file_work_reaches_fbd(self):
        allowed = [("GET", "/"), ("GET", "/main.js"), ("GET", "/api/ls?path=/"), ("PUT", "/api/file?path=/a"),
                   ("POST", "/api/fs/trash"), ("PUT", '/api/workspace?key=web:["","K"]')]
        refused = [("POST", "/api/os/open"), ("POST", "/api/terminal/cd"), ("POST", "/api/web"), ("POST", "/api/update"),
                   ("GET", "/api/health"), ("POST", "/api/view/open"), ("PUT", "/api/workspace?key=w0t0p0:K"),
                   ("POST", "/api/remote/enable"), ("GET", "/../state/token")]
        for m, t in allowed:
            self.assertTrue(proxy.allowed(m, t), (m, t))
        for m, t in refused:
            self.assertFalse(proxy.allowed(m, t), (m, t))

    def test_only_the_event_socket_upgrades(self):
        async def go():
            req = await parse(b"GET /fb/api/ls?path=/ HTTP/1.1\r\nHost: h\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
            with self.assertRaises(httpd.HttpError) as e:
                await proxy.forward(req, 1, "tok")
            self.assertEqual(e.exception.status, 400)
        run(go())

    def test_the_page_never_passes_a_token(self):
        self.assertEqual(proxy.without_token("/api/ws?t=SECRET&client=c"), "/api/ws?client=c")

    def test_fbds_policies_pass_with_only_framing_and_connection_changed(self):
        page = (b"HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ncontent-security-policy: default-src 'self'; "
                b"connect-src 'self' ws://127.0.0.1:47821; frame-ancestors 'none'\r\nconnection: keep-alive\r\n\r\n")
        head = proxy.rewrite_head(page, "10.0.0.5:8765", keep_alive=False).decode()
        self.assertIn("frame-ancestors 'self'", head)
        self.assertIn("connect-src 'self' ws://10.0.0.5:8765 wss://10.0.0.5:8765", head)
        self.assertIn("default-src 'self'", head)
        self.assertTrue(head.endswith("Connection: close\r\n\r\n"))
        raw = b"HTTP/1.1 200 OK\r\ncontent-type: image/svg+xml\r\ncontent-security-policy: sandbox; default-src 'none'\r\n\r\n"
        self.assertIn("content-security-policy: sandbox; default-src 'none'", proxy.rewrite_head(raw, "h", False).decode())


class LabelsTest(unittest.TestCase):
    def test_the_ssh_destination_is_read_as_the_bridge_reads_it(self):
        self.assertEqual(layout.ssh_host('ssh -tt devbox.example "tmux -CC new -A -s main"'), "devbox.example")
        self.assertEqual(layout.ssh_host("ssh -Ap 2222 alex@devbox.example"), "devbox.example")
        self.assertIsNone(layout.ssh_host("zsh"))
        self.assertEqual(layout.tilde("/Users/alex/src"), "~/src")

    def test_status_marks_leave_titles(self):
        for raw, clean in [("✳ Virtual patching", "Virtual patching"), ("◐ Enable web", "Enable web"), ("⠋ build", "build"),
                           ("✻\ufe0e Imunify", "Imunify"), ("mc [root@devbox]", "mc [root@devbox]"), ("100% done", "100% done"),
                           ("✳", "✳")]:
            self.assertEqual(layout.bare(raw), clean)

    def test_coding_agents_by_program_or_start_command(self):
        self.assertEqual(layout.agent_of("claude", ""), "claude")
        self.assertEqual(layout.agent_of("node", "/opt/homebrew/bin/codex --full-auto"), "codex")
        for job, cmd in [("zsh", "-zsh"), ("ssh", "ssh devbox.example tmux -CC"), ("vim", "vim claude.md"), ("", None)]:
            self.assertIsNone(layout.agent_of(job, cmd), (job, cmd))

    def test_addresses(self):
        self.assertEqual(net.host_name("10.0.0.5:8765"), "10.0.0.5")
        self.assertEqual(net.host_name("[::1]:8765"), "::1")
        self.assertEqual(net.urls("127.0.0.1", 8765, ["10.0.0.5"]), ["http://127.0.0.1:8765/"])
        self.assertEqual(net.urls("0.0.0.0", 8765, ["10.0.0.5"]), ["http://127.0.0.1:8765/", "http://10.0.0.5:8765/"])


class Line:
    """A stand-in for iterm2's LineContents."""
    def __init__(self, cells, styles=None, hard_eol=True):
        self.cells, self.styles, self.hard_eol = cells, styles or [None] * len(cells), hard_eol

    def string_at(self, x):
        return self.cells[x]

    def style_at(self, x):
        return self.styles[x]


class ScreenTest(unittest.TestCase):
    def test_skipped_cells_trailing_blanks_and_the_cursor(self):
        line = screen.enc_line(Line(["a", "\x00", "b", " ", " "]), cursor_x=7)
        self.assertEqual(line["r"], [["a b", None, None, 0], ["    ", None, None, 0], [" ", None, None, screen.CURSOR]])
        self.assertTrue(line["e"])

    def test_wide_and_symbol_cells_travel_one_by_one(self):
        self.assertEqual(screen.enc_line(Line(["宽", "", "x"]))["r"], [[["宽", "", "x"], None, None, 0]])


class SiteTest(unittest.TestCase):
    """The web server itself, on a free port, without iTerm2 or fbd."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        verify = config.verifier(config.hash_password("correct horse"))
        cfg = {**config.DEFAULTS, "allow_hosts": []}
        self.site = Site(None, None, cfg, verify, None, [])
        self.server = self.loop.run_until_complete(httpd.serve(self.site.handle, "127.0.0.1", 0, self.site.body_limit))
        self.port = self.server.listener.sockets[0].getsockname()[1]

    def tearDown(self):
        self.server.close()
        self.loop.close()

    def ask(self, method, path, body=None, headers=None):
        async def go():
            def call():
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
                c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
                r = c.getresponse()
                return r.status, dict(r.getheaders()), r.read()
            return await self.loop.run_in_executor(None, call)
        return self.loop.run_until_complete(go())

    def test_signing_in_gives_a_cookie_that_opens_the_rest(self):
        origin = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        self.assertEqual(self.ask("GET", "/auth/check")[0], 401)
        self.assertEqual(self.ask("GET", "/fb/api/ls?path=/")[0], 401)
        status, _, body = self.ask("POST", "/auth/login", {"password": "wrong"}, origin)
        self.assertEqual((status, json.loads(body)["reason"]), (401, "wrong"))
        status, headers, _ = self.ask("POST", "/auth/login", {"password": "correct horse"}, origin)
        self.assertEqual(status, 204)
        cookie = headers["Set-Cookie"].split(";")[0]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(self.ask("GET", "/auth/check", headers={"Cookie": cookie})[0], 204)

    def test_other_sites_and_names_are_refused(self):
        self.assertEqual(self.ask("POST", "/auth/login", {"password": "correct horse"},
                                  {"Content-Type": "application/json"})[0], 403, "no Origin")
        self.assertEqual(self.ask("POST", "/auth/login", {"password": "x"},
                                  {"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.ask("GET", "/", headers={"Host": "evil.example"})[0], 403, "DNS rebinding")
        self.assertEqual(self.ask("GET", "/ws", headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 401)

    def test_guesses_sent_at_once_are_checked_one_by_one(self):
        checked = []
        self.site.auth._verify = lambda pw: checked.append(pw) or False
        origin = {"Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}

        async def go():
            def call():
                c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
                c.request("POST", "/auth/login", body=json.dumps({"password": "guess"}), headers={"Host": f"127.0.0.1:{self.port}", **origin})
                return c.getresponse().status
            return await asyncio.gather(*(self.loop.run_in_executor(None, call) for _ in range(12)))
        statuses = self.loop.run_until_complete(go())
        self.assertLessEqual(len(checked), auth.FREE_ATTEMPTS, "after the free tries the rest wait instead of hashing")
        self.assertIn(429, statuses)

    def test_a_head_that_never_ends_is_closed(self):
        async def go():
            old, httpd.HEAD_TIMEOUT = httpd.HEAD_TIMEOUT, 0.3
            try:
                r, w = await asyncio.open_connection("127.0.0.1", self.port)
                w.write(b"GET / HTTP/1.1\r\nHost: x")
                try:
                    return await asyncio.wait_for(r.read(), 3)
                finally:
                    w.close()
            finally:
                httpd.HEAD_TIMEOUT = old
        self.assertEqual(self.loop.run_until_complete(go()), b"", "closed without an answer")

    def test_the_page_is_cached_by_its_etag(self):
        status, headers, body = self.ask("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.ask("GET", "/", headers={"If-None-Match": headers["ETag"]})[0], 304)
        self.assertEqual(self.ask("GET", "/../web.json")[0], 404)


class MirrorTest(unittest.TestCase):
    def test_a_gone_session_is_named_so_the_page_can_drop_the_error_later(self):
        from fbbridge.web import mirror
        sent = []

        class Ws:
            async def send(self, text):
                sent.append(json.loads(text))

        class Hub:
            app = types.SimpleNamespace(get_session_by_id=lambda sid: None)

            async def release(self, client):
                pass

        client = mirror.Client(None, Hub(), Ws(), 1000, None)
        asyncio.run(client.subscribe("gone-id"))
        self.assertIn({"t": "error", "msg": mirror.CLOSED, "sid": "gone-id"}, sent)
        self.assertIsNone(client.session)

    def test_a_closed_session_needs_no_size_back_and_does_not_stop_opening_another(self):
        from fbbridge.web import mirror
        from fbbridge.web.hub import Hub
        sent = []

        class Gone:
            async def async_set_grid_size(self, size):
                raise RuntimeError("RPCException: SESSION_NOT_FOUND")

        class Ws:
            async def send(self, text):
                sent.append(json.loads(text))

        app = types.SimpleNamespace(get_session_by_id=lambda sid: Gone() if sid == "fitted" else None)
        hub = Hub(app)
        client = mirror.Client(None, hub, Ws(), 1000, None)
        hub.fitted["fitted"] = [(80, 24), {client}]
        old = sys.modules["iterm2"].__dict__.get("util")
        sys.modules["iterm2"].util = types.SimpleNamespace(Size=lambda w, h: (w, h))
        try:
            asyncio.run(client.subscribe("other"))
        finally:
            sys.modules["iterm2"].util = old
        self.assertEqual(hub.fitted, {})
        self.assertEqual(sent[-1]["sid"], "other", "the open went on to its own answer")

    def test_a_failed_action_is_said_in_words_and_logged(self):
        from fbbridge.web import mirror
        sent, logged = [], []

        class Ws:
            def __init__(self):
                self.messages = [json.dumps({"t": "files"}), json.dumps(["not", "an", "object"])]

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.messages:
                    raise StopAsyncIteration
                return self.messages.pop(0)

            async def send(self, text):
                sent.append(json.loads(text))

        class Hub:
            app, clients = None, set()

            async def join(self, client):
                pass

            async def release(self, client):
                pass

        async def files_of(session):
            raise RuntimeError("RPCException: SESSION_NOT_FOUND")
        client = mirror.Client(None, Hub(), Ws(), 1000, files_of)
        client.session = types.SimpleNamespace(session_id="s1")
        old, mirror.log = mirror.log, logged.append
        try:
            asyncio.run(client.run())
        finally:
            mirror.log = old
        self.assertEqual(sent[0]["msg"], "iTerm2 no longer has a session needed to show the session's files.")
        self.assertTrue(sent[1]["msg"].startswith("Could not do that"))
        self.assertTrue(all("SESSION_NOT_FOUND" not in m["msg"] for m in sent))
        self.assertIn("SESSION_NOT_FOUND", logged[0])


class BuildTest(unittest.TestCase):
    def test_the_build_names_every_page_file_and_index_carries_it(self):
        from fbbridge.web import site
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_bytes(b'<meta name="build" content="__BUILD__">')
            (root / "app.js").write_bytes(b"one")
            files, build = site.page_files(root)
            self.assertIn(f'content="{build}"'.encode(), files["/index.html"][0])
            self.assertEqual(files["/"], files["/index.html"])
            (root / "app.js").write_bytes(b"two")
            files2, build2 = site.page_files(root)
            self.assertNotEqual(build, build2)
            self.assertNotEqual(files["/index.html"][2], files2["/index.html"][2], "the page's ETag follows the build")
        self.assertIn(b'name="build" content="__BUILD__"', (site.STATIC / "index.html").read_bytes())

    def test_a_page_hears_the_build_before_anything_else(self):
        from fbbridge.web import mirror
        sent = []

        class Ws:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def send(self, text):
                sent.append(json.loads(text))

        class Hub:
            app, clients = None, set()

            async def join(self, client):
                await client.send({"t": "layout", "groups": []})

            async def release(self, client):
                pass
        asyncio.run(mirror.Client(None, Hub(), Ws(), 1000, None, "abc123").run())
        self.assertEqual([m["t"] for m in sent], ["build", "layout"])
        self.assertEqual(sent[0]["id"], "abc123")


if __name__ == "__main__":
    unittest.main()
