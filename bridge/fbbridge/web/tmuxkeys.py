# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Special keys for a tmux -CC pane (AC-52). The page sends keys as xterm bytes in normal
cursor mode; text sent to a tmux pane arrives unchanged, so a program that switched to
application cursor keys (mc, vim) gets the wrong ones. tmux's own key names are encoded by
tmux for the pane's current mode, so keys the page sends go to tmux by name."""
import re

BASE = {"A": "Up", "B": "Down", "C": "Right", "D": "Left", "H": "Home", "F": "End",
        "P": "F1", "Q": "F2", "R": "F3", "S": "F4"}
TILDE = {"2": "IC", "3": "DC", "5": "PPage", "6": "NPage", "15": "F5", "17": "F6", "18": "F7",
         "19": "F8", "20": "F9", "21": "F10", "23": "F11", "24": "F12"}
MODS = {"2": "S-", "3": "M-", "4": "M-S-", "5": "C-", "6": "C-S-", "7": "C-M-", "8": "C-M-S-"}
KEY = re.compile(r"\x1b(?:\[(?:1;([2-8]))?([ABCDHF])|O([ABCDHFPQRS])|\[(\d{1,2})(?:;([2-8]))?~|\[(Z))")


def key_names(data):
    """tmux key names for `data` when it is made only of special keys, else None (text, a
    paste, or a key tmux could not name: those go to the pane unchanged)."""
    names, i = [], 0
    while i < len(data):
        m = KEY.match(data, i)
        if not m:
            return None
        mod, csi, ss3, num, num_mod, back = m.groups()
        if back:
            name = "BTab"
        elif num:
            if num not in TILDE:
                return None
            name = MODS.get(num_mod, "") + TILDE[num]
        else:
            name = MODS.get(mod, "") + BASE[csi or ss3]
        names.append(name)
        i = m.end()
    return names or None
