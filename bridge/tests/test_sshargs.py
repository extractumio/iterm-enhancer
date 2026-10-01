# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-38: the ssh destination of a remote tmux -CC session, read from its gateway's ssh."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fbbridge.sshargs import key, target  # noqa: E402


class TargetTest(unittest.TestCase):
    def test_tms(self):
        self.assertEqual(target(["ssh", "-tt", "ai4", "tmux -CC new-session -A -f pause-after=5 -s work"]), ["ai4"])

    def test_proxy_command_keeps_its_value_whole(self):
        # an exact argv (sysctl): the ProxyCommand's spaces do not split it
        argv = ["ssh", "-o", "ProxyCommand=ssh -W %h:%p jump", "-tt", "ai4", "tmux", "-CC"]
        self.assertEqual(target(argv), ["-o", "ProxyCommand=ssh -W %h:%p jump", "ai4"])

    def test_port_user_config_jump(self):
        argv = ["/usr/bin/ssh", "-F", "/Users/alex/x", "-J", "jump", "-p", "2222", "-l", "alex", "10.0.0.5", "tmux", "-CC"]
        self.assertEqual(target(argv), ["-F", "/Users/alex/x", "-J", "jump", "-p", "2222", "-l", "alex", "10.0.0.5"])

    def test_session_options_are_dropped(self):
        argv = ["ssh", "-tt4", "-L", "8080:x:80", "-oPort=2200", "-o", "RemoteCommand=tmux", "-o", "RequestTTY yes",
                "-i", "/Users/alex/.ssh/k", "user@vm"]
        self.assertEqual(target(argv), ["-4", "-o", "Port=2200", "-i", "/Users/alex/.ssh/k", "user@vm"])

    def test_autossh_runs_ssh_whose_arguments_count(self):
        # autossh itself is not ssh; its child ssh is found by name and parsed
        self.assertIsNone(target(["autossh", "-M", "0", "vm"]))
        self.assertEqual(target(["ssh", "-tt", "vm", "tmux -CC"]), ["vm"])

    def test_not_ssh_or_no_destination(self):
        self.assertIsNone(target(["mosh", "vm"]))
        self.assertIsNone(target(["ssh", "-p"]))
        self.assertIsNone(target(["ssh", "-tt"]))
        self.assertIsNone(target([]))

    def test_odd_arguments_never_break_the_poll(self):
        self.assertEqual(target(["ssh", "-o", "", "vm"]), ["vm"])
        self.assertIsNone(target(["ssh", "-i", "/Users/al\u00e9x/k", "vm"]), "non-ASCII cannot travel in a header")

    def test_key(self):
        self.assertEqual(key(["-p", "2222", "alex@vm"]), "-p 2222 alex@vm")
        self.assertEqual(key(["ai4"]), "ai4")


if __name__ == "__main__":
    unittest.main()
