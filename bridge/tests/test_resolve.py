# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-37 / AC-12: what a tmux pane is, and what the bridge agrees to type, without iTerm2
(its module is stubbed where it is not installed)."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge import app, resolve  # noqa: E402
from fbbridge.common import LOCAL_HOST  # noqa: E402


def line(host, path="/Users/alex/.ssh", cmd="zsh"):
    return f"{host}\t/tmp/tmux-501/default\t%3\t{path}\t{cmd}"


class ResolveTest(unittest.TestCase):
    def test_a_local_tmux_pane(self):
        r = resolve.tmux_result("tmux -CC", line(LOCAL_HOST))
        self.assertEqual((r["mode"], r["cwd"]), ("tmux -CC", "/Users/alex/.ssh"))

    def test_a_host_named_like_this_mac_behind_ssh_is_remote(self):
        r = resolve.tmux_result("tmux -CC", line(f"{LOCAL_HOST}.evil.example"), via_ssh=True)
        self.assertEqual(r["mode"], "remote")
        self.assertIsNone(r["cwd"], "never this Mac's folder at a path the host chose")

    def test_another_host_name_is_remote(self):
        self.assertEqual(resolve.tmux_result("tmux -CC", line("devbox"))["mode"], "remote")


class TypableTest(unittest.TestCase):
    def test_only_printable_text_is_typed(self):
        self.assertTrue(app.typable("cd '/Users/alex/my dir'"))
        self.assertTrue(app.typable("naïve 文件 "))
        for bad in ("cd x\r", "a\nb", "\x03", "\x1b[200~", "a\x7fb", "a\x85b", None):
            self.assertFalse(app.typable(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
