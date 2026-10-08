# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-54: a tmux -CC integration iTerm2 dropped is detached once, and only then; the
reattach command comes from what was recorded while the connection lived. Without iTerm2."""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
iterm2 = sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge import tmuxwatch  # noqa: E402
from fbbridge.common import UserError  # noqa: E402

STREAM = ["%begin 1 2 0", "%end 1 2 0", "%output %97 hello"]


class Screen:
    def __init__(self, rows):
        self.rows = rows
        self.number_of_lines = len(rows)

    def line(self, i):
        return types.SimpleNamespace(string=self.rows[i])


class Session:
    def __init__(self, sid, rows):
        self.session_id, self.rows, self.typed = sid, rows, []
        self.window = types.SimpleNamespace(window_id="w1")

    async def async_get_screen_contents(self):
        return Screen(list(self.rows))

    async def async_get_variable(self, name):
        return 4242 if name == "pid" else None

    async def async_send_text(self, text, suppress_broadcast=False):
        self.typed.append(text)


class Conn:
    def __init__(self, cid, session):
        self.connection_id, self.owning_session = cid, session

    async def async_send_command(self, command):
        return "/tmp/tmux-1000/default\tmain\n"


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.connections = []
        self.sessions = {}

        async def get_connections(conn):
            return list(self.connections)
        self.patches = [mock.patch.object(iterm2, "async_get_tmux_connections", get_connections, create=True),
                        mock.patch.object(tmuxwatch, "gateway_target", self.target),
                        mock.patch.object(tmuxwatch, "find_descendant", lambda pid, name: None)]
        for p in self.patches:
            p.start()
        app = types.SimpleNamespace(get_session_by_id=lambda sid: self.sessions.get(sid), get_window_by_id=lambda w: None)
        self.watch = tmuxwatch.TmuxWatch(None, app)
        self.watch.alert = self.no_alert
        self.remote = True

    def tearDown(self):
        for p in self.patches:
            p.stop()

    async def target(self, c):
        return ["devbox.example"] if self.remote else None

    async def no_alert(self):
        pass

    def tick(self, now):
        asyncio.run(self.watch.tick(now))

    def gateway(self, rows):
        s = Session("g1", rows)
        self.sessions["g1"] = s
        self.connections = [Conn("c1", s)]
        self.tick(0)                       # seen alive: recorded
        self.connections = []              # then iTerm2 lets go of it
        return s

    def test_fresh_protocol_for_five_seconds_detaches_once(self):
        s = self.gateway(STREAM)
        self.tick(1)
        s.rows = STREAM + ["%output %97 more"]      # still arriving
        self.tick(3)
        self.assertEqual(s.typed, [], "not before 5 s")
        self.assertTrue(self.watch.suspect("g1"), "but no longer mirrored")
        s.rows = STREAM + ["%output %97 more", "%output %97 again"]
        self.tick(5)
        s.rows = STREAM + ["%output %97 again", "%output %97 and again"]
        self.tick(7)
        self.assertEqual(s.typed, ["detach-client\n"])
        self.tick(9)
        self.tick(20)
        self.assertEqual(s.typed, ["detach-client\n"], "at most once")
        [drop] = self.watch.items()
        self.assertEqual((drop["host"], drop["session"]), ("devbox.example", "main"))

    def test_old_protocol_lines_alone_write_nothing(self):
        s = self.gateway(STREAM)                    # the stream stopped: the screen never changes
        for now in (1, 4, 8, 15):
            self.tick(now)
        self.assertEqual(s.typed, [])

    def test_a_session_never_seen_owning_a_connection_is_left_alone(self):
        s = Session("own", STREAM)                  # e.g. the user ran tmux -CC by hand
        self.sessions["own"] = s
        for now, extra in ((0, "a"), (3, "b"), (8, "c")):
            s.rows = STREAM + [f"%output %1 {extra}"]
            self.tick(now)
        self.assertEqual(s.typed, [])

    def test_text_that_only_starts_with_percent_is_not_protocol(self):
        s = self.gateway(["%5 done", "100% complete"])
        s.rows = ["%6 done", "100% complete"]
        self.tick(1)
        self.tick(8)
        self.assertEqual(s.typed, [])

    def test_a_local_gateway_needs_its_tmux_process(self):
        self.remote = False
        s = self.gateway(STREAM)
        self.tick(1)
        s.rows = STREAM + ["%output %97 more"]
        self.tick(7)
        self.assertEqual(s.typed, [], "no tmux under the session: its input may be a shell")

    def test_a_gateway_closing_mid_countdown_is_forgotten(self):
        self.gateway(STREAM)
        self.tick(1)
        del self.sessions["g1"]
        self.tick(7)
        self.assertEqual((self.watch.gateways, self.watch.items()), ({}, []))

    def test_a_shell_prompt_under_old_protocol_lines_is_never_detached(self):
        s = self.gateway(STREAM)                    # tmux ended; ssh's shell came back below the old lines
        s.rows = STREAM + ["user@devbox:~$ "]
        self.tick(1)
        for now, typed in ((3, "l"), (5, "ls"), (7, "ls -la"), (9, "ls -la /"), (12, "ls -la /tmp")):
            s.rows = STREAM + [f"user@devbox:~$ {typed}"]
            self.tick(now)
        self.assertEqual(s.typed, [])

    def test_one_change_then_a_frozen_screen_is_not_a_stream(self):
        s = self.gateway(STREAM)
        self.tick(1)
        s.rows = STREAM + ["%output %97 once"]
        self.tick(3)
        for now in (5, 7, 9, 12):
            self.tick(now)
        self.assertEqual(s.typed, [])

    def test_a_gateway_attached_again_on_a_new_connection_is_left_alone(self):
        s = self.gateway(STREAM)
        self.connections = [Conn("c2", s)]           # the user ran tmux -CC attach again in the same session
        for now, extra in ((1, "a"), (3, "b"), (5, "c"), (7, "d"), (9, "e")):
            s.rows = STREAM + [f"%output %1 {extra}"]
            self.tick(now)
        self.assertEqual(s.typed, [])

    def test_a_gateway_that_shows_protocol_is_suspect_at_once(self):
        self.gateway(STREAM)
        self.tick(1)
        self.assertTrue(self.watch.suspect("g1"), "web input stops before the screen has changed")

    def test_a_normal_end_forgets_the_gateway(self):
        s = self.gateway(["user@devbox:~$ "])
        for now in range(1, 30, 2):
            self.tick(now)
        self.assertEqual((self.watch.gateways, s.typed), ({}, []))


class AttachTest(unittest.TestCase):
    def test_remote_and_local_commands_quote_what_was_recorded(self):
        remote = {"host": "devbox.example", "target": ["-p", "2222", "devbox.example"], "socket": "/tmp/tmux-1000/my sock", "session": "main"}
        cmd = tmuxwatch.attach_command(remote)
        self.assertTrue(cmd.startswith("ssh -tt -o RemoteCommand=none"), cmd)
        self.assertIn("-p 2222", cmd)
        self.assertIn("attach-session -t main", cmd)
        self.assertIn("'/tmp/tmux-1000/my sock'", cmd)
        local = tmuxwatch.attach_command({"host": "this Mac", "target": None, "socket": "/tmp/s", "session": "work"})
        self.assertTrue(local.endswith("-S /tmp/s -CC attach-session -t work"), local)
        with self.assertRaises(UserError):
            tmuxwatch.attach_command({"host": "devbox.example", "target": ["devbox.example"], "socket": None, "session": None})


class LinesTest(unittest.TestCase):
    def test_wrapped_protocol_lines_are_whole_lines(self):
        # as seen in the incident: long %extended-output lines wrap, the newest row is a continuation
        rows = [("%extended-output %97 0 : \\033[?25l\\033[2D", False), ("\\033[4B\\015", True),
                ("%extended-output %97 0 : \\033[7A✶\\033[3", False), ("9m\\015\\033[?25h", True)]
        self.assertIsNotNone(tmuxwatch.protocol_screen(rows))
        self.assertIsNone(tmuxwatch.protocol_screen(rows + [("user@devbox:~$ ", True)]), "a prompt below them is not a stream")


if __name__ == "__main__":
    unittest.main()
