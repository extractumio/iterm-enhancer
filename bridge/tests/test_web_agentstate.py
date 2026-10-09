# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: a coding agent's state for the session list, from its title marks (measured: Claude
Code turns ◐ ◑ ◒ ◓ while it works, shows ✳ at rest) and its screen's last rows."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web.agentstate import claude_marked, state_of  # noqa: E402

FOOTER = ["────", "❯ ", "────", "  ~/p Opus 5.5 ctx:47%"]


class StateTest(unittest.TestCase):
    def test_title_marks(self):
        self.assertEqual(state_of("◐ Enable web", FOOTER), "working")
        self.assertEqual(state_of("◓ Enable web", FOOTER), "working")
        self.assertEqual(state_of("✳ Enable web", FOOTER), "done")
        self.assertTrue(claude_marked("✳ x") and claude_marked("◑ x"))
        self.assertFalse(claude_marked("mc [root@devbox]") or claude_marked(""))

    def test_the_screen_tells_waiting_and_working(self):
        ask = ["Do you want to proceed?", "❯ 1. Yes", "  2. No", "Esc to cancel"]
        self.assertEqual(state_of("✳ x", ask), "waiting")
        self.assertEqual(state_of("◐ x", ask), "waiting", "a question wins over the spinner")
        self.assertEqual(state_of("", ["✻ Thinking… (12s · esc to interrupt)"] + FOOTER), "working", "Codex and Claude without a title mark")

    def test_claude_codes_status_line_without_title_marks(self):
        # measured on Claude Code 2.1.292: no "esc to interrupt"; the cells between words read as NUL
        working = ["\u273b\x00Combobulating\u2026 (6m\x008s\x00\u00b7\x00\u2193\x0031.5k\x00tokens)", ""] + FOOTER
        done = ["\u273b Worked for 2m 12s \u00b7 done 8:17 AM \u00b7 1 shell still running", ""] + FOOTER
        self.assertEqual(state_of("Demo stand preparation", working), "working")
        self.assertEqual(state_of("Demo stand preparation", done), "done")

    def test_a_failure_that_ended_the_turn(self):
        err = ["⎿  API Error: 529 {\"type\":\"overloaded_error\"}"] + FOOTER
        self.assertEqual(state_of("✳ x", err), "failed")
        self.assertEqual(state_of("◐ x", err), "working", "still going after an error: not failed")

    def test_only_the_last_rows_count(self):
        old = ["Do you want to proceed?"] + ["output"] * 40
        self.assertEqual(state_of("✳ x", old), "done")
        self.assertEqual(state_of("✳ x", ["Do you want to proceed?"] + ["output"] * 14), "done", "a question 15 rows up is history")


class TmuxPaneTest(unittest.TestCase):
    """A tmux pane's program comes from tmux: iTerm2 says "ssh" (the gateway), and a name the user
    gave the window or pane hides Claude Code's title marks."""

    def test_a_named_tmux_pane_running_claude_is_an_agent(self):
        import asyncio
        from unittest import mock
        from fbbridge.web import layout

        class Tc:
            connection_id = "c1"

            async def async_send_command(self, cmd):
                assert cmd.startswith("list-panes -a")
                assert "#{automatic-rename}" in cmd
                return "%97 0 claude Demo stand preparation and fixes\n%99 1 mc mc\n%5  zsh zsh\n"

        async def conns(connection):
            return [Tc()]

        class Session:
            session_id = "s97"

            async def async_get_variable(self, name):
                return {"name": "Demo stand preparation and fixes", "jobName": "ssh", "processTitle": "",
                        "commandLine": 'ssh -tt devbox.example "tmux -CC new-session -A -s main"', "path": "/home/alex/p",
                        "profileName": "tmux", "tmuxWindowPane": "97"}.get(name, "")

            async def async_get_screen_contents(self):
                rows = ["\u273b Worked for 2m 12s \u00b7 done", "\u276f "]
                return types.SimpleNamespace(number_of_lines=len(rows), line=lambda i: types.SimpleNamespace(string=rows[i]))
        tab = types.SimpleNamespace(tmux_window_id="5", tmux_connection_id="c1")
        tv = {"titleOverride": "", "tmuxWindowName": "Demo stand preparation and fixes", "tmuxWindowTitle": "main:5"}
        with mock.patch.object(layout.iterm2, "async_get_tmux_connections", conns, create=True):
            progs = asyncio.run(layout.tmux_programs(types.SimpleNamespace(connection=None)))
            item = asyncio.run(layout.session_item(Session(), tab, tv, 1, 1, 1, progs))
        self.assertEqual(progs, {("c1", "%97"): ("claude", False, "Demo stand preparation and fixes"), ("c1", "%99"): ("mc", True, "mc"),
                                 ("c1", "%5"): ("zsh", None, "zsh")}, "an older tmux leaves the flag empty")
        self.assertEqual((item["agent"], item["state"]), ("claude", "done"))
        self.assertIn("claude", item["sub"])

    def test_the_line_below_names_program_folder_then_titles(self):
        import asyncio
        from fbbridge.web import layout

        def session(**v):
            class S:
                session_id = "s1"

                async def async_get_variable(self, name):
                    return v.get(name, "")

                async def async_get_screen_contents(self):
                    return types.SimpleNamespace(number_of_lines=0, line=lambda i: None)
            return S()
        plain = types.SimpleNamespace(tmux_window_id=None)
        tv = {"titleOverride": "web app", "tmuxWindowName": "", "tmuxWindowTitle": ""}
        item = asyncio.run(layout.session_item(session(terminalWindowName="\u2733 Fix the list", jobName="claude", path="/Users/alex/p"),
                                               plain, tv, 1, 1, 1))
        self.assertEqual(item["sub"], ["claude", "~/p", "Fix the list"])
        # a tmux pane whose title is still "ssh" from before, now running claude: no "ssh"
        tmux = types.SimpleNamespace(tmux_window_id="5", tmux_connection_id="c1")
        tv = {"titleOverride": "", "tmuxWindowName": "Build the databases", "tmuxWindowTitle": "main:5"}
        item = asyncio.run(layout.session_item(session(name="ssh", jobName="ssh", path="/home/alex/p", tmuxWindowPane="101"),
                                               tmux, tv, 1, 1, 1, {("c1", "%101"): ("claude", False, "Build the databases")}))
        self.assertEqual(item["sub"], ["claude", "~/p"])
        # a one-word name the user gave stays the title; tmux's own name (the program) is not repeated
        tv = {"titleOverride": "", "tmuxWindowName": "ssh", "tmuxWindowTitle": "main:5"}
        named = asyncio.run(layout.session_item(session(name="Fix the list", path="/home/alex/p", tmuxWindowPane="101"),
                                                tmux, tv, 1, 1, 1, {("c1", "%101"): ("claude", False, "api")}))
        self.assertEqual((named["title"], named["sub"]), ("api", ["claude", "~/p", "Fix the list"]), "the live name, not iTerm2's old one")
        auto = asyncio.run(layout.session_item(session(name="Fix the list", path="/home/alex/p", tmuxWindowPane="101"),
                                               tmux, tv, 1, 1, 1, {("c1", "%101"): ("claude", True, "claude")}))
        self.assertEqual((auto["title"], auto["sub"]), ("Fix the list", ["claude", "~/p"]))


if __name__ == "__main__":
    unittest.main()
