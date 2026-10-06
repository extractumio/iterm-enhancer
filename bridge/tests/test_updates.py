# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-51: the update notice. A local server plays GitHub; nothing leaves the machine."""
import http.server
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import common, updates  # noqa: E402


class Releases(http.server.BaseHTTPRequestHandler):
    hits = []

    def do_HEAD(self):
        Releases.hits.append((self.path, self.headers.get("User-Agent"), self.headers.get("Cookie")))
        if self.path == "/rel/latest":
            self.send_response(302); self.send_header("Location", f"/rel/tag/{Releases.tag}")
        elif self.path.startswith("/rel/tag/"):
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()

    def log_message(self, *a):
        pass


class VersionTest(unittest.TestCase):
    def test_versions_compare_as_numbers_and_only_releases_count(self):
        self.assertTrue(updates.newer("v0.19.0", "v0.18.0"))
        self.assertTrue(updates.newer("v0.10.0", "v0.9.0"), "not as text")
        self.assertTrue(updates.newer("v1.0.0", "v0.99.99"))
        self.assertFalse(updates.newer("v0.18.0", "v0.18.0"))
        self.assertFalse(updates.newer("v0.17.0", "v0.18.0"))
        for current in ("dev", "30fa800-a13657b1", "", None):
            self.assertFalse(updates.newer("v9.0.0", current), current)
        for latest in ("v1.2", "v1.2.3-rc1", "<b>v1.2.3", "v1.2.3\n", None, ""):
            self.assertFalse(updates.newer(latest, "v0.1.0"), latest)


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Releases)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/rel"
        Releases.hits.clear()

    def test_the_tag_comes_from_the_redirect_and_nothing_else_is_sent(self):
        Releases.tag = "v0.19.0"
        self.assertEqual(updates.latest_tag(self.url), "v0.19.0")
        self.assertEqual([h[0] for h in Releases.hits], ["/rel/latest", "/rel/tag/v0.19.0"])
        self.assertTrue(all(h[1] == "iterm-filebrowser-update-check" and h[2] is None for h in Releases.hits))

    def test_a_tag_that_is_not_a_release_is_dropped(self):
        for tag in ("nightly", "v1.2.3-rc1", "v1.2.3%3Cb%3E"):
            Releases.tag = tag
            self.assertIsNone(updates.latest_tag(self.url), tag)

    def test_a_missing_page_raises_for_the_caller_to_ignore(self):
        with self.assertRaises(Exception) as caught:
            updates.latest_tag(self.url + "-gone")
        caught.exception.close()


class UpdatesTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        for patch in (mock.patch.object(common, "LOG_DIR", d / "logs"),  # never the live log
                      mock.patch.object(updates, "OFF", d / "no-update-check"),
                      mock.patch.object(updates, "SKIPPED", d / "update-skipped"),
                      mock.patch.object(updates, "CHECKED", d / "update-checked")):
            patch.start()
            self.addCleanup(patch.stop)
        self.posts = []
        self.fetched = []

    def make(self, current="v0.18.0", latest="v0.19.0"):
        def fetch():
            self.fetched.append(1)
            return latest
        self.now = 1_000_000.0
        return updates.Updates(lambda path, body: self.posts.append((path, body)), current, fetch, lambda: self.now)

    async def test_a_newer_release_is_announced(self):
        u = self.make()
        await u.check()
        self.assertEqual(self.posts, [("/internal/update", {"latest": "v0.19.0"})])

    async def test_the_same_or_an_older_release_announces_nothing(self):
        for latest in ("v0.18.0", "v0.17.0"):
            self.posts.clear()
            await self.make(latest=latest).check()
            self.assertEqual(self.posts, [("/internal/update", {"latest": None})], latest)

    async def test_a_build_that_is_not_a_release_never_asks(self):
        await self.make(current="30fa800-a13657b1").check()
        self.assertEqual((self.fetched, self.posts), ([], []))

    async def test_a_failed_check_is_silent(self):
        def boom():
            raise OSError("offline")
        u = updates.Updates(lambda *a: self.posts.append(a), "v0.18.0", boom)
        await u.check()
        self.assertEqual(self.posts, [])

    async def test_skip_hides_that_version_but_not_the_next(self):
        u = self.make()
        await u.check()
        await u.choose("skip", "v0.19.0")
        self.assertEqual(self.posts[-1], ("/internal/update", {"latest": None}))
        u.latest = "v0.20.0"
        self.assertEqual(u.notice(), "v0.20.0")

    async def test_off_stops_the_network_and_on_checks_again(self):
        u = self.make()
        await u.check()
        await u.choose("off")
        self.assertEqual(self.posts[-1], ("/internal/update", {"latest": None}))
        self.fetched.clear()
        await u.check()
        self.assertEqual(self.fetched, [], "no request while off")
        await u.choose("on")
        self.assertEqual(self.fetched, [1])
        self.assertEqual(self.posts[-1], ("/internal/update", {"latest": "v0.19.0"}))

    async def test_it_asks_once_a_day_by_the_wall_clock_and_remembers_across_restarts(self):
        u = self.make()
        await u.tick()
        await u.tick()
        self.assertEqual(self.fetched, [1], "not again within the day")
        self.now += updates.EVERY + 1
        await u.tick()
        self.assertEqual(self.fetched, [1, 1])
        restarted = updates.Updates(lambda p, b: self.posts.append((p, b)), "v0.18.0", lambda: self.fetched.append(2), lambda: self.now)  # a bridge restart (an upgrade)
        self.assertEqual(restarted.latest, "v0.19.0", "… keeps the last answer")
        self.posts.clear()
        await restarted.tick()
        self.assertEqual((self.fetched, self.posts), ([1, 1], [("/internal/update", {"latest": "v0.19.0"})]), "tells fbd, asks nobody")

    async def test_a_failed_check_or_post_is_tried_again_at_the_next_tick(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("offline")
            return "v0.19.0"
        u = updates.Updates(lambda p, b: self.posts.append((p, b)), "v0.18.0", flaky, lambda: 1_000_000.0)
        await u.tick()
        self.assertEqual(self.posts, [])
        await u.tick()                                       # an hour later, not a day
        self.assertEqual(self.posts, [("/internal/update", {"latest": "v0.19.0"})])
        broken = [True]

        def post(path, body):
            if broken[0]:
                raise ConnectionError("fbd away")
            self.posts.append((path, body))
        u2 = updates.Updates(post, "v0.18.0", lambda: "v0.20.0", lambda: 2_000_000.0)
        self.posts.clear()
        await u2.tick()
        broken[0] = False
        await u2.tick()
        self.assertEqual(self.posts, [("/internal/update", {"latest": "v0.20.0"})])

    async def test_a_bad_skip_is_ignored(self):
        u = self.make()
        await u.check()
        await u.choose("skip", "../../etc/passwd")
        self.assertFalse(updates.SKIPPED.exists())


if __name__ == "__main__":
    unittest.main()
