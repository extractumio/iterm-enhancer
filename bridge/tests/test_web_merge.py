# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: "Merge windows" moves plain tabs into one window and each tmux session's tabs into
one window of their own, keeps the Files viewer apart, and fails loud when iTerm2's windows
change meanwhile. Without iTerm2: a fake app with the API's shapes."""
import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web import merge  # noqa: E402


class Profile:
    def __init__(self, command):
        self.all_properties = {"Custom Command": command}


class Session:
    def __init__(self, sid, command="No"):
        self.session_id, self.command = sid, command

    async def async_get_profile(self):
        return Profile(self.command)


class Tab:
    def __init__(self, tid, conn=None, browser=False):
        self.tab_id = tid
        self.sessions = [Session(f"s-{tid}", "Browser" if browser else "No")]
        self.tmux_window_id, self.tmux_connection_id = ("@" + tid, conn) if conn else ("-1", "")


class Window:
    def __init__(self, app, wid, tabs):
        self.app, self.window_id, self.tabs = app, wid, tabs

    async def async_set_tabs(self, tabs):
        """iTerm2's reorder: the given tabs first, the window's others after; empty windows close."""
        self.app.calls.append((self.window_id, [t.tab_id for t in tabs]))
        ids = {t.tab_id for t in tabs}
        for w in self.app.terminal_windows:
            w.tabs = [t for t in w.tabs if t.tab_id not in ids]
        self.tabs = list(tabs) + self.tabs
        self.app.terminal_windows = [w for w in self.app.terminal_windows if w.tabs]


class App:
    def __init__(self, layout):
        self.calls, self.refreshes = [], 0
        self.terminal_windows = [Window(self, wid, tabs) for wid, tabs in layout]

    async def async_refresh(self):
        self.refreshes += 1

    def get_window_by_id(self, wid):
        return next((w for w in self.terminal_windows if w.window_id == wid), None)

    def get_tab_by_id(self, tid):
        return next((t for w in self.terminal_windows for t in w.tabs if t.tab_id == tid), None)

    def shape(self):
        return [(w.window_id, [t.tab_id for t in w.tabs]) for w in self.terminal_windows]


def run(app, shown=None):
    return asyncio.run(merge.merge_windows(app, app.terminal_windows, shown))


class MergeTest(unittest.TestCase):
    def test_plain_tabs_gather_in_the_fullest_window_and_tmux_sessions_in_their_own(self):
        app = App([("w1", [Tab("a")]),
                   ("w2", [Tab("b"), Tab("c")]),
                   ("w3", [Tab("m1", "conn-1")]),
                   ("w4", [Tab("m2", "conn-1"), Tab("m3", "conn-1")]),
                   ("w5", [Tab("r1", "conn-2")])])
        text = run(app)
        self.assertEqual(app.shape(), [("w2", ["b", "c", "a"]), ("w4", ["m2", "m3", "m1"]), ("w5", ["r1"])])
        self.assertEqual(text, "Merged 5 windows into 3.")

    def test_the_shown_sessions_window_is_the_target_of_its_kind(self):
        app = App([("w1", [Tab("a")]), ("w2", [Tab("b"), Tab("c")])])
        run(app, shown="s-a")
        self.assertEqual(app.shape(), [("w1", ["a", "b", "c"])])

    def test_the_files_viewer_stays_where_it_is(self):
        app = App([("w1", [Tab("a")]), ("viewer", [Tab("v", browser=True)]), ("w2", [Tab("b")])])
        text = run(app)
        self.assertEqual(app.shape(), [("w1", ["a", "b"]), ("viewer", ["v"])])
        self.assertEqual(text, "Merged 3 windows into 2.")

    def test_a_plain_tab_leaves_a_mixed_window_and_the_window_stays_for_its_tmux_tab(self):
        app = App([("w1", [Tab("a"), Tab("b")]), ("w2", [Tab("c"), Tab("m1", "conn-1")])])
        text = run(app)
        self.assertEqual(app.shape(), [("w1", ["a", "b", "c"]), ("w2", ["m1"])])
        self.assertEqual(text, "Merged 2 windows into 2.")

    def test_nothing_to_merge_moves_nothing(self):
        app = App([("w1", [Tab("a")]), ("w2", [Tab("m1", "conn-1")]), ("viewer", [Tab("v", browser=True)])])
        text = run(app)
        self.assertEqual((app.calls, text[:17]), ([], "Nothing to merge:"))

    def test_a_tab_that_closed_meanwhile_fails_loud(self):
        app = App([("w1", [Tab("a")]), ("w2", [Tab("b")])])
        windows = list(app.terminal_windows)

        async def closed():
            app.terminal_windows = [app.terminal_windows[0]]      # w2 closed after the plan
        app.async_refresh = closed
        with self.assertRaises(merge.MergeError):
            asyncio.run(merge.merge_windows(app, windows))
        self.assertEqual(app.calls, [])


if __name__ == "__main__":
    unittest.main()
