# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Special keys for a tmux -CC pane (AC-52). The page sends keys as xterm bytes in normal
cursor mode; text sent to a tmux pane arrives unchanged, so a program that switched to
application cursor keys (mc, vim) gets the wrong ones. tmux's own key names are encoded by
tmux for the pane's current mode, so keys the page sends go to tmux by name."""
import re

import iterm2

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


async def type_into(conn, session, data):
    """Text as it is; special keys for a tmux pane by tmux's names, which tmux encodes for the
    pane's cursor mode (mc and vim switch to application cursor keys). Says which way it went."""
    names = key_names(data)
    tab = session.tab
    if names and tab and tab.tmux_window_id not in (None, "-1"):
        pane = await session.async_get_variable("tmuxWindowPane")
        tc = await iterm2.async_get_tmux_connection_by_connection_id(conn, tab.tmux_connection_id)
        if tc and pane not in (None, ""):
            await tc.async_send_command(f"send-keys -t %{str(pane).lstrip('%')} {' '.join(names)}")
            return "keys"
    await session.async_send_text(data, suppress_broadcast=True)
    return "text"
