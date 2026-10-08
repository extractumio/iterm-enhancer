# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: the pencil names a pane: its iTerm2 tab and session, and a tmux pane's window and
pane title; the name stays plain text. Without iTerm2: fakes with the API's shapes."""
import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
iterm2 = sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web import rename  # noqa: E402


class Tab:
    def __init__(self, tmux=None):
        self.tmux_window_id, self.tmux_connection_id = (tmux, "conn-1") if tmux else (None, None)
        self.titles = []

    async def async_set_title(self, title):
        self.titles.append(title)


class Session:
    def __init__(self, tab, pane="3"):
        self.tab, self.names, self.pane = tab, [], pane

    async def async_set_name(self, name):
        self.names.append(name)

    async def async_get_variable(self, name):
        return {"tmuxWindowPane": self.pane, "profileName": "Default"}.get(name)


class Tmux:
    def __init__(self):
        self.commands = []

    async def async_send_command(self, command):
        self.commands.append(command)
        return ""


class RenameTest(unittest.TestCase):
    def setUp(self):
        self.tmux = Tmux()
        self.saved = getattr(iterm2, "async_get_tmux_connection_by_connection_id", None)

        async def by_id(conn, cid):
            return self.tmux if cid == "conn-1" else None
        iterm2.async_get_tmux_connection_by_connection_id = by_id

    def tearDown(self):
        iterm2.async_get_tmux_connection_by_connection_id = self.saved

    def test_a_pane_names_its_tab_and_session_as_plain_text(self):
        s = Session(Tab())
        asyncio.run(rename.rename(None, s, "  build \\(user.name) "))
        self.assertEqual(s.tab.titles, ["build \\\\(user.name)"], "a backslash cannot start an interpolation")
        self.assertEqual(s.names, ["build \\\\(user.name)"])
        self.assertEqual(self.tmux.commands, [])

    def test_a_tmux_pane_renames_its_window_and_titles_the_pane(self):
        s = Session(Tab(tmux="7"))
        asyncio.run(rename.rename(None, s, 'MR "135" $HOME'))
        self.assertEqual(self.tmux.commands, ['rename-window -t @7 "MR \\"135\\" \\$HOME"',
                                              'select-pane -t %3 -T "MR \\"135\\" \\$HOME"'])
        self.assertEqual(s.tab.titles, [], "the tab shows the tmux window's name")

    def test_tmux_formats_and_commands_stay_text(self):
        self.assertEqual(rename.tmux_word("#(rm -rf ~) #{pane_id}"), '"##(rm -rf ~) ##{pane_id}"')

    def test_an_empty_name_gives_the_names_back(self):
        s = Session(Tab(tmux="7"))
        asyncio.run(rename.rename(None, s, "   "))
        self.assertEqual(self.tmux.commands, ["set-window-option -u -t @7 automatic-rename", 'select-pane -t %3 -T ""'])
        self.assertEqual((s.tab.titles, s.names), ([], ["Default"]))
        plain = Session(Tab())
        asyncio.run(rename.rename(None, plain, ""))
        self.assertEqual((plain.tab.titles, plain.names), ([""], ["Default"]))

    def test_refusals(self):
        for name in ["two\nlines", "bell\x07", "x" * 101, "c1\x9b", "a\u2028b", "bidi\u202e", "iso\u2067", None, 7]:
            with self.subTest(name=repr(name)[:12]), self.assertRaises(rename.RenameError):
                asyncio.run(rename.rename(None, Session(Tab()), name))
        lost = Session(Tab(tmux="7"))
        lost.tab.tmux_connection_id = "gone"
        with self.assertRaisesRegex(rename.RenameError, "no longer connected"):
            asyncio.run(rename.rename(None, lost, "x"))
        unknown = Session(Tab(tmux="7"), pane=None)
        with self.assertRaisesRegex(rename.RenameError, "which tmux pane"):
            asyncio.run(rename.rename(None, unknown, "x"))
        self.assertEqual(self.tmux.commands, [], "nothing is half done")


class HandlerTest(unittest.TestCase):
    """The page's {"t": "rename"}: the pane by id, the name, then the list at once."""

    def run_rename(self, sid, name):
        import json
        from fbbridge.web import mirror
        sent, refreshed = [], []

        class Ws:
            async def send(self, text):
                sent.append(json.loads(text))

        class Hub:
            app = types.SimpleNamespace(get_session_by_id=lambda i: Session(Tab()) if i == "live" else None)

            async def refresh(self):
                refreshed.append(True)
        asyncio.run(mirror.Client(None, Hub(), Ws(), 1000, None).handle({"t": "rename", "id": sid, "name": name}))
        return sent, refreshed

    def test_a_named_pane_shows_in_the_list_at_once(self):
        self.assertEqual(self.run_rename("live", "build"), ([], [True]))

    def test_a_gone_pane_or_a_refused_name_says_so_for_that_pane(self):
        from fbbridge.web import mirror
        sent, refreshed = self.run_rename("gone", "build")
        self.assertEqual((sent, refreshed), ([{"t": "error", "msg": mirror.CLOSED, "sid": "gone"}], []))
        sent, refreshed = self.run_rename("live", "a\nb")
        self.assertEqual((sent[0]["sid"], "line breaks" in sent[0]["msg"], refreshed), ("live", True, []))


if __name__ == "__main__":
    unittest.main()
