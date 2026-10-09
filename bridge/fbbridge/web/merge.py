# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
""""Merge windows" under the web app's session list (AC-52): iTerm2's tabs move into as few
windows as the session list can show, without raising a window. Plain tabs go into one
window and each tmux -CC session's tabs into one window of their own: the list shows a window
holding a tmux tab as that tmux session (its [+] opens tmux tabs), and iTerm2 ignores a plain
tab moved into a tmux window. Tabs of a browser profile (the Files viewer) stay where they are.
iTerm2's own "Merge All Windows" is a menu item, disabled while iTerm2 is not the active app."""
from ..viewer_profile import BROWSER


class MergeError(Exception):
    """Why the windows were not merged, for the page's status line."""


def is_tmux(tab):
    return tab.tmux_window_id not in (None, "-1")


async def is_browser(tab):
    for s in tab.sessions:
        if (await s.async_get_profile()).all_properties.get("Custom Command") == BROWSER:
            return True
    return False


async def plan(windows, shown_sid=None):
    """[(target window id, [tab ids to move into it])] and the number of windows left after."""
    groups = {}                  # "" for plain tabs, else the tmux connection id: [(window, tab)]
    for w in windows:
        for t in w.tabs:
            if not await is_browser(t):
                groups.setdefault(t.tmux_connection_id if is_tmux(t) else "", []).append((w, t))
    moves = []
    for key in sorted(groups, key=bool):          # plain tabs first, as the list shows them
        pairs = groups[key]
        counts = {}
        for w, _ in pairs:
            counts[w.window_id] = counts.get(w.window_id, 0) + 1
        shown = next((w.window_id for w, t in pairs if any(s.session_id == shown_sid for s in t.sessions)), None)
        target = shown or max(counts, key=counts.get)       # the first of the fullest on a tie
        moved = [t.tab_id for w, t in pairs if w.window_id != target]
        if moved:
            moves.append((target, moved))
    targets = {target for target, _ in moves}
    going = {tid for _, tids in moves for tid in tids}
    left = [w for w in windows if w.window_id in targets or any(t.tab_id not in going for t in w.tabs)]
    return moves, len(left)          # a window keeps living while a tab stays in it


async def merge_windows(app, windows, shown_sid=None):
    """Move the tabs of `windows` (iTerm2 Window objects of `app`); "Merged …" or "Nothing …"."""
    moves, after = await plan(windows, shown_sid)
    if not moves:
        return "Nothing to merge: each window already holds a different kind of session."
    for target_id, tab_ids in moves:
        await app.async_refresh()                 # an earlier move changed the windows' tabs
        target = app.get_window_by_id(target_id)
        tabs = [app.get_tab_by_id(tid) for tid in tab_ids]
        if not target or None in tabs:
            raise MergeError("iTerm2's windows changed meanwhile: press Merge windows again.")
        await target.async_set_tabs(target.tabs + tabs)
    await app.async_refresh()
    return f"Merged {len(windows)} windows into {after}."
