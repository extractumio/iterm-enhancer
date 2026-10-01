# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-26: the viewer profile is repaired when iTerm2 holds it as a terminal profile, and a
failed bridge command reaches the panel that asked. Runs without iTerm2."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fbbridge import backend, viewer_profile  # noqa: E402
from fbbridge.common import UserError  # noqa: E402
from fbbridge.viewer_profile import BROWSER, NOT_BROWSER, ensure_browser, install_viewer_profile  # noqa: E402


def kinds(*values):
    """A fake kind(): returns the values in turn, then the last one forever."""
    seq = list(values)

    async def kind():
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return kind


class EnsureBrowserTest(unittest.TestCase):
    def setUp(self):
        self.rewrites = 0
        patcher = mock.patch.object(viewer_profile, "log", lambda _: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rewrite(self):
        self.rewrites += 1

    def run_ensure(self, kind):
        asyncio.run(ensure_browser(kind, self.rewrite, timeout=0.2, step=0.05))

    def test_browser_profile_is_left_alone(self):
        self.run_ensure(kinds(BROWSER))
        self.assertEqual(self.rewrites, 0)

    def test_terminal_profile_is_reloaded(self):
        self.run_ensure(kinds("No", "No", BROWSER))
        self.assertEqual(self.rewrites, 1)

    def test_missing_profile_is_written(self):
        self.run_ensure(kinds(None, BROWSER))
        self.assertEqual(self.rewrites, 1)

    def test_never_a_browser_fails_loud(self):
        with self.assertRaises(UserError) as cm:
            self.run_ensure(kinds("No"))
        self.assertEqual(str(cm.exception), NOT_BROWSER)
        self.assertEqual(self.rewrites, 1, "one rewrite, then wait; no rewrite storm")


class InstallTest(unittest.TestCase):
    def test_force_rewrites_same_content(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.json"
            install_viewer_profile(path=path)
            self.assertEqual(json.loads(path.read_text())["Profiles"][0]["Custom Command"], BROWSER)
            before = path.stat().st_mtime_ns
            install_viewer_profile(path=path)
            self.assertEqual(path.stat().st_mtime_ns, before, "unchanged content is not rewritten")
            install_viewer_profile(force=True, path=path)
            self.assertGreater(path.stat().st_mtime_ns, before, "force writes again (iTerm2 reloads)")


class ReportFailureTest(unittest.TestCase):
    def setUp(self):
        self.b = backend.Backend()
        self.posted, self.logged = [], []
        self.b.post = lambda path, body: self.posted.append((path, body))
        patcher = mock.patch.object(backend, "log", self.logged.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_user_error_goes_to_the_asking_panel_as_is(self):
        self.b.report_failure({"action": "viewer", "by": "p1"}, UserError(NOT_BROWSER))
        self.assertEqual(self.posted, [("/internal/error", {"message": NOT_BROWSER, "by": "p1"})])
        self.assertIn(NOT_BROWSER, self.logged[0])

    def test_other_errors_name_the_command(self):
        self.b.report_failure({"action": "type"}, RuntimeError("SESSION_NOT_FOUND"))
        self.assertEqual(self.posted[0][1], {"message": "type failed: RuntimeError: SESSION_NOT_FOUND", "by": None})

    def test_undeliverable_error_is_logged(self):
        def boom(*_):
            raise ConnectionError("fbd down")
        self.b.post = boom
        self.b.report_failure({"action": "viewer", "by": "p1"}, UserError("x"))
        self.assertTrue(any("not delivered" in m for m in self.logged))


if __name__ == "__main__":
    unittest.main()
