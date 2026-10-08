# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: an image pasted in the web app is saved where the pane's shell runs, and its path
comes back for the page to paste. Without iTerm2; the host is a fake ssh running here."""
import asyncio
import http.client
import json
import os
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge import agentctl  # noqa: E402
from fbbridge.web import config, httpd, paste  # noqa: E402
from fbbridge.web.site import Site  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class NameTest(unittest.TestCase):
    def test_only_the_four_image_types_whose_bytes_match(self):
        self.assertRegex(paste.checked_name("image/png", PNG), paste.NAME)
        self.assertTrue(paste.checked_name("image/jpeg", b"\xff\xd8\xff\xe0rest").endswith(".jpg"))
        self.assertTrue(paste.checked_name("image/webp", b"RIFF\0\0\0\0WEBPVP8 ").endswith(".webp"))
        for ctype, data in [("image/heic", b"\0\0\0\x18ftypheic"), ("text/plain", b"hi"), ("", PNG),
                            ("image/png", b"GIF89a"), ("image/png", b"")]:
            with self.subTest(ctype=ctype, data=data[:8]), self.assertRaises(paste.PasteError):
                paste.checked_name(ctype, data)

    def test_too_large(self):
        with self.assertRaisesRegex(paste.PasteError, "20 MB"):
            paste.checked_name("image/png", PNG + b"\0" * paste.MAX_BYTES)


class LocalTest(unittest.TestCase):
    def test_a_private_file_in_a_private_folder_and_old_pastes_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "paste"
            folder.mkdir(mode=0o755)
            old = folder / "old.png"
            old.write_bytes(PNG)
            os.utime(old, (time.time() - 8 * 86400,) * 2)
            path = Path(paste.save_local("20261007-120000-0123abcd.png", PNG, folder))
            self.assertEqual(path.read_bytes(), PNG)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            self.assertFalse(old.exists())

    def test_a_planted_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            elsewhere = Path(tmp) / "elsewhere"
            elsewhere.mkdir()
            link = Path(tmp) / "paste"
            link.symlink_to(elsewhere)
            with self.assertRaises(paste.PasteError):
                paste.save_local("20261007-120000-0123abcd.png", PNG, link)


class RemoteTest(unittest.TestCase):
    """The host is a fake ssh that runs the command here, with HOME in a temporary folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        fake = root / "ssh"
        fake.write_text(f'#!/bin/bash\nexport HOME="{self.home}"\nexec bash -c "${{@: -1}}"\n')
        fake.chmod(0o755)
        self.old, agentctl.SSH = agentctl.SSH, str(fake)

    def tearDown(self):
        agentctl.SSH = self.old
        self.tmp.cleanup()

    def test_the_host_keeps_the_file_and_answers_its_path(self):
        name = "20261007-120000-0123abcd.png"
        path = paste.save_remote(["devbox.example"], name, PNG)
        self.assertEqual(path, str(self.home / paste.REMOTE_DIR / name))
        self.assertEqual(Path(path).read_bytes(), PNG)
        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)

    def test_the_home_of_a_host_is_asked_once(self):
        from fbbridge.web.access import WebAccess
        web = WebAccess(None, None, None, None)
        calls = []
        real = agentctl.ssh

        def counting(*a, **k):
            calls.append(a)
            return real(*a, **k)
        agentctl.ssh = counting
        try:
            first = asyncio.run(web.home_of("devbox.example", ["devbox.example"]))
            again = asyncio.run(web.home_of("devbox.example", ["devbox.example"]))
        finally:
            agentctl.ssh = real
        self.assertEqual((first, again, len(calls)), (str(self.home), str(self.home), 1))

    def test_only_paste_names_reach_the_remote_shell(self):
        with self.assertRaises(paste.PasteError):
            paste.remote_script('x"; rm -rf ~; ".png')

    def test_a_failing_host_says_why(self):
        agentctl.SSH = "/usr/bin/false"
        with self.assertRaises(paste.PasteError):
            paste.save_remote(["devbox.example"], "20261007-120000-0123abcd.png", PNG)


class UploadTest(unittest.TestCase):
    def test_any_file_keeps_its_name_in_safe_letters(self):
        name = paste.upload_name("Report Q3 (final).pdf", b"%PDF-1.7")
        self.assertRegex(name, r"^\d{8}-\d{6}-[0-9a-f]{8}-Report_Q3_final_.pdf$")
        self.assertTrue(paste.NAME.fullmatch(name), "it may go into the remote shell line")
        for bad in ["../../etc/passwd", "a'; rm -rf ~; '.txt", "$(touch x)", "", "...", "日本語.txt"]:
            n = paste.upload_name(bad, b"x")
            self.assertTrue(paste.NAME.fullmatch(n), (bad, n))
            self.assertNotRegex(n.split("-", 3)[3], r"[/'$ ;()]")
        with self.assertRaises(paste.PasteError):
            paste.upload_name("a.txt", b"")
        with self.assertRaises(paste.PasteError):
            paste.upload_name("a.txt", b"0" * (paste.MAX_BYTES + 1))


class RouteTest(unittest.TestCase):
    """POST /paste on a real server: sign-in, the session, the type, the answer."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir, paste.local_dir = paste.local_dir, lambda: Path(self.tmp.name) / "paste"
        verify = config.verifier(config.hash_password("correct horse"))
        app = types.SimpleNamespace(get_session_by_id=lambda sid: object() if sid == "live" else None)

        async def paste_to(session):
            return None                                  # this Mac
        self.site = Site(None, app, {**config.DEFAULTS, "allow_hosts": []}, verify, None, [], paste_to)
        self.server = self.loop.run_until_complete(httpd.serve(self.site.handle, "127.0.0.1", 0, self.site.body_limit))
        self.port = self.server.listener.sockets[0].getsockname()[1]
        self.origin = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.server.close()
        self.loop.close()
        paste.local_dir = self.old_dir
        self.tmp.cleanup()

    def ask(self, method, path, body=b"", headers=None):
        def call():
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        return self.loop.run_until_complete(self.loop.run_in_executor(None, call))

    def sign_in(self):
        _, headers, _ = self.ask("POST", "/auth/login", json.dumps({"password": "correct horse"}).encode(),
                                 {"Origin": self.origin, "Content-Type": "application/json"})
        return headers["Set-Cookie"].split(";")[0]

    def test_a_signed_in_page_gets_the_saved_path(self):
        h = {"Origin": self.origin, "Content-Type": "image/png", "Cookie": self.sign_in()}
        status, _, body = self.ask("POST", "/paste?sid=live", PNG, h)
        self.assertEqual(status, 200, body)
        path = Path(json.loads(body)["path"])
        self.assertEqual((path.parent, path.read_bytes()), (Path(self.tmp.name) / "paste", PNG))

    def test_an_uploaded_file_of_any_kind(self):
        h = {"Origin": self.origin, "Content-Type": "application/octet-stream", "Cookie": self.sign_in()}
        status, _, body = self.ask("POST", "/upload?sid=live&name=notes%20v2.md", b"# notes\n", h)
        self.assertEqual(status, 200, body)
        path = Path(json.loads(body)["path"])
        self.assertTrue(path.name.endswith("-notes_v2.md"))
        self.assertEqual(path.read_bytes(), b"# notes\n")
        self.assertEqual(self.ask("POST", "/upload?sid=live&name=x", b"x", {**h, "Cookie": ""})[0], 401)

    def test_refusals(self):
        cookie = self.sign_in()
        h = {"Origin": self.origin, "Content-Type": "image/png", "Cookie": cookie}
        self.assertEqual(self.ask("POST", "/paste?sid=live", PNG, {**h, "Cookie": ""})[0], 401)   # not signed in, and small
        self.assertEqual(self.ask("POST", "/paste?sid=live", PNG, {**h, "Origin": "http://evil.example"})[0], 403)
        status, _, body = self.ask("POST", "/paste?sid=gone", PNG, h)
        self.assertEqual((status, "closed" in json.loads(body)["error"]), (404, True))
        status, _, body = self.ask("POST", "/paste?sid=live", b"\0\0\0\x18ftypheic", {**h, "Content-Type": "image/heic"})
        self.assertEqual((status, "PNG, JPEG" in json.loads(body)["error"]), (422, True))
        self.assertFalse((Path(self.tmp.name) / "paste").exists())

    def test_an_unsigned_upload_stays_small(self):
        h = {"Origin": self.origin, "Content-Type": "image/png"}
        self.assertEqual(self.ask("POST", "/paste?sid=live", PNG + b"\0" * 5000, h)[0], 413)

    def test_two_uploads_at_once_and_a_closed_one_frees_its_place(self):
        class Writer:
            def __init__(self):
                self.closed = False

            def is_closing(self):
                return self.closed
        self.site.signed_in = lambda req: True
        reqs = [types.SimpleNamespace(method="POST", path="/paste", writer=Writer()) for _ in range(3)]
        self.assertEqual(self.site.body_limit(reqs[0]), paste.MAX_BYTES)
        self.assertEqual(self.site.body_limit(reqs[1]), paste.MAX_BYTES)
        with self.assertRaises(httpd.HttpError) as e:
            self.site.body_limit(reqs[2])
        self.assertEqual(e.exception.status, 429)
        reqs[0].writer.closed = True
        self.assertEqual(self.site.body_limit(reqs[2]), paste.MAX_BYTES)


class DeadlineTest(unittest.TestCase):
    """A head and a small body get HEAD_TIMEOUT; a large body a signed-in page may send, BODY_TIMEOUT."""

    def read_slowly(self, limit):
        async def go():
            reader = asyncio.StreamReader()
            reader.feed_data(b"POST /paste HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\n\r\n")
            asyncio.get_running_loop().call_later(0.4, reader.feed_data, b"abc")
            return await httpd.read_request(reader, None, lambda req: limit)
        old = httpd.HEAD_TIMEOUT, httpd.BODY_TIMEOUT
        httpd.HEAD_TIMEOUT, httpd.BODY_TIMEOUT = 0.2, 2
        try:
            return asyncio.run(go())
        finally:
            httpd.HEAD_TIMEOUT, httpd.BODY_TIMEOUT = old

    def test_a_large_body_may_take_longer_than_a_head(self):
        self.assertEqual(self.read_slowly(paste.MAX_BYTES).body, b"abc")

    def test_a_small_body_may_not(self):
        with self.assertRaises(TimeoutError):
            self.read_slowly(httpd.SMALL_BODY)


if __name__ == "__main__":
    unittest.main()
