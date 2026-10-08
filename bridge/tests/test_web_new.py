# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: [+] on a group makes a new session there: a tab with the window's current profile,
or a window of its tmux session. Without iTerm2: a fake app with the API's shapes."""
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
iterm2 = sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web import newsession  # noqa: E402


class Session:
    def __init__(self, sid, profile="Default"):
        self.session_id, self.profile = sid, profile

    async def async_get_variable(self, name):
        return self.profile if name == "profileName" else None


class Tab:
    def __init__(self, tid, session=None, tmux=None):
        self.tab_id, self.current_session = tid, session
        self.tmux_window_id, self.tmux_connection_id = (tmux, "conn-1") if tmux else (None, None)


class Window:
    def __init__(self, wid, tabs):
        self.window_id, self.tabs = wid, tabs
        self.current_tab = tabs[0]
        self.made = []

    async def async_create_tab(self, profile=None):
        self.made.append(profile)
        tab = Tab("t-new", Session("s-new", profile))
        self.tabs.append(tab)
        return tab


class App:
    def __init__(self, windows):
        self.terminal_windows = windows

    def get_window_by_id(self, wid):
        return next((w for w in self.terminal_windows if w.window_id == wid), None)

    def get_tab_by_id(self, tid):
        return next((t for w in self.terminal_windows for t in w.tabs if t.tab_id == tid), None)


class NewSessionTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(iterm2, k, None) for k in ("async_get_tmux_connection_by_connection_id", "TmuxException", "MainMenu", "PartialProfile", "Window")}
        iterm2.TmuxException = type("TmuxException", (Exception,), {})

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(iterm2, k, v)

    def test_a_window_gets_a_tab_with_its_current_profile(self):
        w = Window("w1", [Tab("t1", Session("s1", "devbox ssh"))])
        sid = asyncio.run(newsession.new_session(None, App([w]), "w1"))
        self.assertEqual((sid, w.made), ("s-new", ["devbox ssh"]))

    def tmux_app(self, appears_after=0.0):
        """A tmux window w2; iTerm2's New Tmux Tab adds tab t-tmux to it (after `appears_after` s)."""
        w = Window("w2", [Tab("t1", Session("s1"), tmux="@1")])
        other = Window("w9", [Tab("t9", Session("s9"))])
        app = App([other, w])
        app.current_terminal_window, app.activated = other, []

        async def app_activate():
            app.activated.append("app")
        app.async_activate = app_activate
        for win in (w, other):
            async def activate(win=win):
                app.activated.append(win.window_id)
                app.current_terminal_window = win
            win.async_activate = activate

        async def menu(conn, item):
            assert item == "tmux.New Tmux Tab" and app.current_terminal_window is w, "acts on the key window"
            async def add():
                await asyncio.sleep(appears_after)
                w.tabs.append(Tab("t-tmux", Session("s-tmux"), tmux="@2"))
            asyncio.ensure_future(add())

        async def by_id(conn, cid):
            return object()
        iterm2.MainMenu = types.SimpleNamespace(async_select_menu_item=menu)
        iterm2.async_get_tmux_connection_by_connection_id = by_id
        return app, w

    def test_a_tmux_group_gets_a_tmux_tab_in_its_window_and_the_key_window_back(self):
        app, w = self.tmux_app()
        self.assertEqual(asyncio.run(newsession.new_session(None, app, "w2")), "s-tmux")
        self.assertEqual(w.made, [], "no local shell tab in a tmux window")
        self.assertEqual(app.activated, ["app", "w2", "w9"], "the window that was key before is given back")

    def test_a_tmux_tab_the_app_learns_of_late_is_still_found(self):
        app, _ = self.tmux_app(appears_after=0.05)
        self.assertEqual(asyncio.run(newsession.new_session(None, app, "w2")), "s-tmux")

    def test_a_closed_window_or_lost_tmux_says_so(self):
        with self.assertRaisesRegex(newsession.NewSessionError, "closed"):
            asyncio.run(newsession.new_session(None, App([]), "gone"))

        async def none(conn, cid):
            return None
        iterm2.async_get_tmux_connection_by_connection_id = none
        w = Window("w2", [Tab("t1", Session("s1"), tmux="@1")])
        with self.assertRaisesRegex(newsession.NewSessionError, "no longer connected"):
            asyncio.run(newsession.new_session(None, App([w]), "w2"))

    def test_a_session_that_never_appears_is_an_error_not_a_hang(self):
        w = Window("w1", [Tab("t1", Session("s1"))])

        async def empty_tab(profile=None):
            return Tab("t-empty")
        w.async_create_tab = empty_tab
        old, newsession.SETTLE_GAP = newsession.SETTLE_GAP, 0
        try:
            with self.assertRaisesRegex(newsession.NewSessionError, "did not report"):
                asyncio.run(newsession.new_session(None, App([w]), "w1"))
        finally:
            newsession.SETTLE_GAP = old


class CreateTest(unittest.TestCase):
    """The browser that pressed + gets the list with the new session, then the order to show it."""

    def test_layout_then_created_and_a_second_press_within_a_second_is_refused(self):
        from fbbridge.web import mirror
        sent, made = [], []

        class Ws:
            async def send(self, text):
                sent.append(json.loads(text))

        class Hub:
            app, layout = None, [{"wid": "w1", "items": [{"id": "s-new"}]}]

            async def refresh(self):
                pass

        async def fake_new(conn, app, wid):
            made.append(wid)
            return "s-new"
        old, mirror.new_session = mirror.new_session, fake_new
        try:
            client = mirror.Client(None, Hub(), Ws(), 1000, None)

            async def twice():
                await client.handle({"t": "new", "group": "w1"})
                await client.handle({"t": "new", "group": "w1"})
            asyncio.run(twice())
        finally:
            mirror.new_session = old
        self.assertEqual(made, ["w1"])
        self.assertEqual([m["t"] for m in sent], ["layout", "created", "error"])
        self.assertEqual(sent[1]["id"], "s-new")
        self.assertIn("One new session a second", sent[2]["msg"])


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(iterm2, k, None) for k in ("PartialProfile", "Window")}

        def prof(name, command=None):
            return types.SimpleNamespace(name=name, all_properties={"Custom Command": command} if command else {})

        async def query(conn, properties=None):
            return [prof("tmux"), prof("Files Viewer", "Browser"), prof("Default"), prof("devbox ssh")]

        async def default(conn):
            return prof("Default")
        iterm2.PartialProfile = types.SimpleNamespace(async_query=query, async_get_default=default)

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(iterm2, k, v)

    def test_terminal_profiles_default_first_browsers_left_out(self):
        self.assertEqual(asyncio.run(newsession.profiles(None)), ["Default", "devbox ssh", "tmux"])

    def test_a_new_window_with_a_known_profile_only(self):
        made = []
        app = App([])

        async def create(conn, profile=None):
            made.append(profile)
            w = Window("w-new", [Tab("t-new", Session("s-new", profile))])
            app.terminal_windows.append(w)
            return w
        iterm2.Window = types.SimpleNamespace(async_create=create)
        self.assertEqual(asyncio.run(newsession.new_window(None, app, "tmux")), "s-new")
        with self.assertRaisesRegex(newsession.NewSessionError, "no profile"):
            asyncio.run(newsession.new_window(None, app, "Files Viewer"))
        self.assertEqual(made, ["tmux"])


class ReorderTest(unittest.TestCase):
    def test_tabs_follow_the_rows_and_only_this_windows_tabs_move(self):
        moved = []
        a, b, c = Tab("t1", Session("s1")), Tab("t2", Session("s2")), Tab("t3", Session("s3"))
        for t in (a, b, c):
            t.current_session.tab = t
        w = Window("w1", [a, b, c])
        other = Tab("t9", Session("s9"))
        other.current_session.tab = other
        app = App([w, Window("w2", [other])])
        app.get_session_by_id = lambda sid: next((t.current_session for win in app.terminal_windows for t in win.tabs if t.current_session.session_id == sid), None)

        async def set_tabs(tabs):
            moved.append([t.tab_id for t in tabs])
        w.async_set_tabs = set_tabs
        asyncio.run(newsession.reorder_tabs(app, "w1", ["s3", "s1"]))
        self.assertEqual(moved, [["t3", "t1", "t2"]], "the rest keep their order after the moved ones")
        with self.assertRaisesRegex(newsession.NewSessionError, "out of date"):
            asyncio.run(newsession.reorder_tabs(app, "w1", ["s9", "s1"]))
        self.assertEqual(len(moved), 1, "another window's tab is never pulled in")


if __name__ == "__main__":
    unittest.main()
