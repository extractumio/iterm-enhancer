# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-30: the bridge follows a pane while any terminal window is open, also when no window has
the focus (the focused one closed and none took it): fbd must not see it disconnected."""
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import app  # noqa: E402


class Sess:
    def __init__(self, sid):
        self.session_id = sid


class Win:
    def __init__(self, wid, sess):
        self.window_id, self.current_tab = wid, types.SimpleNamespace(current_session=sess)


class App:
    def __init__(self, windows, focused=None):
        self.terminal_windows, self.current_terminal_window = windows, focused

    def get_session_by_id(self, sid):
        return next((w.current_tab.current_session for w in self.terminal_windows if w.current_tab.current_session.session_id == sid), None)

    def get_window_and_tab_for_session(self, sess):
        return next(((w, w.current_tab) for w in self.terminal_windows if w.current_tab.current_session is sess), (None, None))


class FollowTest(unittest.IsolatedAsyncioTestCase):
    async def target(self, iterm, last=None, viewer=()):
        f = app.Follower(None, iterm, types.SimpleNamespace(is_viewer=lambda s: s.session_id in viewer))
        f.last = last
        with mock.patch.object(app, "static_vars", mock.AsyncMock(return_value=(1,))):
            win, sess = await f.target()
        return win and win.window_id, sess and sess.session_id

    async def test_the_focused_pane(self):
        a, b = Win("w1", Sess("s1")), Win("w2", Sess("s2"))
        self.assertEqual(await self.target(App([a, b], focused=b), last={"session": "s1"}), ("w2", "s2"))

    async def test_no_window_in_focus_the_last_pane_while_it_is_open_else_another_window_s(self):
        a, b = Win("w1", Sess("s1")), Win("w2", Sess("s2"))
        self.assertEqual(await self.target(App([a, b]), last={"session": "s2"}), ("w2", "s2"))
        self.assertEqual(await self.target(App([a, b]), last={"session": "gone"}), ("w1", "s1"),
                         "its window closed: an open terminal window's pane")
        self.assertEqual(await self.target(App([a, b]), last=None), ("w1", "s1"), "a bridge that just started")

    async def test_a_viewer_in_front_and_nothing_to_follow(self):
        viewer, a = Win("v", Sess("view")), Win("w1", Sess("s1"))
        self.assertEqual(await self.target(App([viewer, a], focused=viewer), viewer={"view"}), ("w1", "s1"))
        self.assertEqual(await self.target(App([])), (None, None), "no terminal window: the panels say so")


if __name__ == "__main__":
    unittest.main()
