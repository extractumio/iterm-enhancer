# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: special keys for a tmux pane go by tmux's names, so tmux encodes them for the pane's
cursor mode (application cursor keys in mc and vim); text goes as it is."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web.tmuxkeys import key_names  # noqa: E402


class KeyNamesTest(unittest.TestCase):
    def test_keys_by_name(self):
        self.assertEqual(key_names("\x1b[A"), ["Up"])
        self.assertEqual(key_names("\x1bOD\x1bOD"), ["Left", "Left"])
        self.assertEqual(key_names("\x1b[1;2D\x1b[1;5C"), ["S-Left", "C-Right"])
        self.assertEqual(key_names("\x1bOP\x1b[15~\x1b[24~"), ["F1", "F5", "F12"])
        self.assertEqual(key_names("\x1b[5~\x1b[6~\x1b[3~\x1b[H\x1b[F\x1b[Z"), ["PPage", "NPage", "DC", "Home", "End", "BTab"])

    def test_text_pastes_and_unnamed_keys_go_as_they_are(self):
        for data in ["ls\r", "\x1b", "\x1b[13;2u", "\x1b[200~text\x1b[201~", "\x1b[A ls", "", "\x1b[99~", "\x03"]:
            self.assertIsNone(key_names(data), repr(data))


if __name__ == "__main__":
    unittest.main()
