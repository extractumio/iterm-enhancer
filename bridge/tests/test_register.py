# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-36: a bridge restart inside one iTerm2 process does not register the tool again
(that reloads every panel as a new web view and drops its window binding)."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import app, registry  # noqa: E402


class RegisterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        (self.dir / "token").write_text("t1\n")
        self.marker = self.dir / "registered.json"
        self.register = mock.AsyncMock()
        iterm2 = sys.modules["iterm2"]
        self.addCleanup(lambda old=getattr(iterm2, "tool", None): setattr(iterm2, "tool", old) if old else delattr(iterm2, "tool"))
        iterm2.tool = types.SimpleNamespace(async_register_web_view_tool=self.register)
        for patch in (mock.patch.object(app, "APP_DIR", self.dir), mock.patch.object(registry, "MARKER", self.marker),
                      mock.patch.object(app, "heal", mock.AsyncMock()),
                      mock.patch.object(app, "_registered", {"url": None})):
            patch.start()
            self.addCleanup(patch.stop)

    async def test_the_same_url_in_the_same_iterm2_process_is_not_registered_again(self):
        await app.register(None, "e:1:100")
        self.assertEqual(self.register.await_count, 1)
        app._registered["url"] = None                       # a new bridge process
        await app.register(None, "e:1:100")
        self.assertEqual(self.register.await_count, 1)

    async def test_a_new_iterm2_process_or_token_registers(self):
        await app.register(None, "e:1:100")
        app._registered["url"] = None
        await app.register(None, "e:2:200")                  # iTerm2 was restarted
        self.assertEqual(self.register.await_count, 2)
        app._registered["url"] = None
        (self.dir / "token").write_text("t2\n")              # fbd made a new token (AC-07)
        await app.register(None, "e:2:200")
        self.assertEqual(self.register.await_count, 3)

    async def test_without_an_epoch_it_always_registers(self):
        await app.register(None, None)
        app._registered["url"] = None
        await app.register(None, None)
        self.assertEqual(self.register.await_count, 2)

    def test_the_marker_holds_no_token(self):
        registry.remember("http://127.0.0.1:1/?t=secret", "e:1:100", self.marker)
        self.assertNotIn("secret", self.marker.read_text())
        self.assertFalse(registry.registered_here("u", "e:1:100", Path("/nonexistent/marker")))
        self.marker.write_text("not json")
        self.assertFalse(registry.registered_here("u", "e:1:100", self.marker))


if __name__ == "__main__":
    unittest.main()
