# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: [+] on a group makes a new session right after the selected pane, in its folder: a
tab with its profile, or a tmux window of its tmux session. Without iTerm2: a fake app with the
API's shapes."""
import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
iterm2 = sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web import newsession  # noqa: E402


class Session:
    def __init__(self, sid, profile="Default", pane=None):
        self.session_id, self.profile, self.pane = sid, profile, pane

    async def async_get_variable(self, name):
        return {"profileName": self.profile, "tmuxWindowPane": self.pane}.get(name)


class Tab:
    def __init__(self, tid, session=None, tmux=None):
        self.tab_id, self.current_session = tid, session
        self.sessions = [session] if session else []
        self.tmux_window_id, self.tmux_connection_id = (tmux, "conn-1") if tmux else (None, None)


class Window:
    def __init__(self, wid, tabs):
        self.window_id, self.tabs = wid, tabs
        self.current_tab = tabs[0]
        self.made, self.orders = [], []

    async def async_create_tab(self, profile=None, index=None, profile_customizations=None):
        self.made.append((profile, index, profile_customizations and profile_customizations.values))
        tab = Tab("t-new", Session("s-new", profile))
        self.tabs.insert(len(self.tabs) if index is None else index, tab)
        return tab

    async def async_set_tabs(self, tabs):
        self.orders.append([t.tab_id for t in tabs])
        self.tabs = list(tabs)


class App:
    def __init__(self, windows):
        self.terminal_windows = windows

    def get_window_by_id(self, wid):
        return next((w for w in self.terminal_windows if w.window_id == wid), None)

    def get_tab_by_id(self, tid):
        return next((t for w in self.terminal_windows for t in w.tabs if t.tab_id == tid), None)


class Profile:
    def __init__(self):
        self.values = {}

    def _simple_set(self, key, value):
        self.values[key] = value


class Tmux:
    """A tmux -CC connection: new-window adds tab @7 to `into` (a window of its own when None)."""

    def __init__(self, app, into=None, path="/srv/app", fail=False):
        self.app, self.into, self.path, self.fail, self.commands = app, into, path, fail, []

    async def async_send_command(self, command):
        self.commands.append(command)
        if command.startswith("display"):
            return self.path + "\n"
        if self.fail:
            raise iterm2.TmuxException("no server")
        tab = Tab("t-tmux", Session("s-tmux", pane="9"), tmux="7")
        if self.into:
            self.into.tabs.append(tab)
        else:
            self.app.terminal_windows.append(Window("w-own", [tab]))
        return "@7\n"


def run(app, wid, shown=None):
    return asyncio.run(newsession.new_session(None, app, wid, shown))


class NewSessionTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(iterm2, k, None) for k in ("async_get_tmux_connection_by_connection_id", "TmuxException", "LocalWriteOnlyProfile")}
        self.saved_resolve = newsession.resolve
        iterm2.TmuxException = type("TmuxException", (Exception,), {})
        iterm2.LocalWriteOnlyProfile = Profile
        self.folder = tempfile.mkdtemp()
        self.resolved = {"mode": "zsh", "cwd": self.folder}

        async def resolve(conn, session):
            return self.resolved
        newsession.resolve = resolve

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(iterm2, k, v)
        newsession.resolve = self.saved_resolve

    def plain(self):
        w = Window("w1", [Tab("t1", Session("s1", "devbox ssh")), Tab("t2", Session("s2", "Default")), Tab("t3", Session("s3"))])
        return App([w]), w

    def test_a_tab_opens_after_the_shown_pane_with_its_profile_and_folder(self):
        app, w = self.plain()
        self.assertEqual(run(app, "w1", shown="s2"), ("s-new", None))
        self.assertEqual(w.made, [("Default", 2, {"Custom Directory": "Yes", "Working Directory": self.folder})])
        self.assertEqual([t.tab_id for t in w.tabs], ["t1", "t2", "t-new", "t3"])

    def test_a_pane_shown_elsewhere_leaves_the_windows_current_pane_selected(self):
        app, w = self.plain()
        run(app, "w1", shown="s-other-window")
        self.assertEqual(w.made[0][:2], ("devbox ssh", 1), "after iTerm2's current tab, with its profile")

    def test_an_unknown_or_closed_folder_opens_the_profiles_folder_and_says_why(self):
        app, w = self.plain()
        self.resolved = {"mode": "remote", "cwd": None, "note": "ssh session"}
        sid, note = run(app, "w1", shown="s1")
        self.assertEqual((sid, w.made[0][2]), ("s-new", None))
        self.assertIn("profile's folder", note)
        self.assertIn("ssh session", note)
        self.resolved = {"mode": "zsh", "cwd": self.folder + "/gone"}
        self.assertIn("cannot be opened", run(app, "w1", shown="s1")[1])

    def tmux_app(self, into_own_window=False, **kw):
        w = Window("w2", [Tab("t1", Session("s1", pane="3"), tmux="@1"), Tab("t2", Session("s2", pane="4"), tmux="@2")])
        app = App([w])
        tc = Tmux(app, None if into_own_window else w, **kw)

        async def by_id(conn, cid):
            return tc
        iterm2.async_get_tmux_connection_by_connection_id = by_id
        return app, w, tc

    def test_a_tmux_window_opens_after_the_shown_panes_window_in_its_folder(self):
        app, w, tc = self.tmux_app(path="/srv/it's #(x) $HOME")
        self.assertEqual(run(app, "w2", shown="s1"), ("s-tmux", None))
        self.assertEqual(tc.commands, ["display -p -t %3 '#{pane_current_path}'",
                                       """new-window -a -t @1 -c "/srv/it's ##(x) \\$HOME" -P -F '#{window_id}'"""])
        self.assertEqual([t.tab_id for t in w.tabs], ["t1", "t-tmux", "t2"])

    def test_a_tmux_window_iterm2_opens_as_a_window_moves_in_after_the_pane(self):
        app, w, tc = self.tmux_app(into_own_window=True)
        run(app, "w2", shown="s2")
        self.assertEqual(w.orders, [["t1", "t2", "t-tmux"]])

    def test_a_folder_with_a_line_break_never_reaches_tmuxs_command_line(self):
        app, w, tc = self.tmux_app(path='/tmp/x\nrun-shell "touch PWNED"')
        sid, note = run(app, "w2", shown="s1")
        self.assertEqual(tc.commands[-1], "new-window -a -t @1 -P -F '#{window_id}'")
        self.assertIn("control characters", note)
        app, w, tc = self.tmux_app(path="")
        self.assertIn("does not know", run(app, "w2", shown="s1")[1])

    def test_a_closed_window_lost_or_refusing_tmux_says_so(self):
        with self.assertRaisesRegex(newsession.NewSessionError, "closed"):
            run(App([]), "gone")
        app, w, tc = self.tmux_app(fail=True)
        with self.assertRaisesRegex(newsession.NewSessionError, "tmux did not open a window"):
            run(app, "w2")

        async def none(conn, cid):
            return None
        iterm2.async_get_tmux_connection_by_connection_id = none
        with self.assertRaisesRegex(newsession.NewSessionError, "no longer connected"):
            run(app, "w2")

    def test_a_session_that_never_appears_is_an_error_not_a_hang(self):
        app, w = self.plain()

        async def empty_tab(**kw):
            return Tab("t-empty")
        w.async_create_tab = empty_tab
        old, newsession.SETTLE_GAP = newsession.SETTLE_GAP, 0
        try:
            with self.assertRaisesRegex(newsession.NewSessionError, "did not report"):
                run(app, "w1")
            app, w, tc = self.tmux_app()
            tc.into = types.SimpleNamespace(tabs=[])           # iTerm2 never shows it
            with self.assertRaisesRegex(newsession.NewSessionError, "tmux opened window @7"):
                run(app, "w2")
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

        async def fake_new(conn, app, wid, shown):
            made.append(wid)
            return "s-new", "a note"
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
        self.assertEqual((sent[1]["id"], sent[1]["note"]), ("s-new", "a note"))
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
