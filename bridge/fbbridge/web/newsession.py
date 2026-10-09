# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""New sessions from the web app's session list (AC-52): [+] on a group opens a new tab in that
iTerm2 window right after the selected pane's tab, with its profile and in its folder or, in
a tmux -CC window, a new tmux window as that tab; "New window" opens a window with a profile
the user picks."""
import asyncio
import os

import iterm2

from ..resolve import resolve
from ..viewer_profile import BROWSER
from .rename import CONTROL, tmux_word

SETTLE_TRIES = 10          # iTerm2 may report a new tab before its session
TMUX_TRIES = 50            # 5 s: a tmux tab through ssh
SETTLE_GAP = 0.1


class NewSessionError(Exception):
    """Why no session was made, for the page's status line."""


def tmux_tab(window):
    return next((t for t in window.tabs if t.tmux_window_id not in (None, "-1")), None)


async def profile_of(window):
    """The profile [+] opens in this window: its current session's."""
    s = window.current_tab.current_session if window.current_tab else None
    return (await s.async_get_variable("profileName") or "") if s else ""


def selected(window, shown_sid):
    """The pane [+] opens after: the page's shown pane when it is in `window`, else the
    window's current pane in iTerm2. (tab, session)"""
    for t in window.tabs:
        for s in t.sessions:
            if s.session_id == shown_sid:
                return t, s
    t = window.current_tab
    return t, (t.current_session if t else None)


async def new_session(conn, app, window_id, shown_sid=None):
    """[+]: (the new session's id, a note shown over it or None)."""
    window = app.get_window_by_id(window_id)
    if not window:
        raise NewSessionError("This window has closed.")
    tab, session = selected(window, shown_sid)
    if tmux_tab(window):
        if not tab or tab.tmux_window_id in (None, "-1"):       # a plain tab in a mixed window
            tab = tmux_tab(window)
            session = tab.current_session
        return await new_tmux_window(conn, app, window, tab, session)
    return await new_tab(conn, app, window, tab, session)


async def local_folder(conn, session):
    """The folder of `session` on this Mac, and None; or None and why it is not known."""
    if not session:
        return None, "no pane is selected"
    try:
        r = await resolve(conn, session)
    except (RuntimeError, OSError) as e:
        return None, str(e)
    cwd = r.get("cwd")
    if r.get("mode") == "remote" or not cwd:
        return None, f"the pane's folder is not known on this Mac ({r.get('note') or r.get('mode')})"
    if not (os.path.isdir(cwd) and os.access(cwd, os.X_OK)):
        return None, f"{cwd} cannot be opened"
    return cwd, None


async def new_tab(conn, app, window, tab, session):
    """A tab right after `tab`, with `session`'s profile and in its folder."""
    profile = (await session.async_get_variable("profileName") or "") if session else ""
    cwd, why = await local_folder(conn, session)
    custom = None
    if cwd:
        custom = iterm2.LocalWriteOnlyProfile()
        custom._simple_set("Custom Directory", "Yes")
        custom._simple_set("Working Directory", cwd)
    index = next((n + 1 for n, t in enumerate(window.tabs) if tab and t.tab_id == tab.tab_id), None)
    try:
        made = await window.async_create_tab(profile=profile or None, index=index, profile_customizations=custom)
    except Exception as e:  # iTerm2's CreateTabException: a profile renamed or deleted
        raise NewSessionError(f"iTerm2 did not open a tab with the profile {profile or 'Default'}: {e}") from e

    def find():
        return (app.get_tab_by_id(made.tab_id) or made) if made else None
    sid = await settled(find, "iTerm2 did not report the new session.")
    return sid, f"The new tab starts in its profile's folder: {why}." if why else None


async def new_tmux_window(conn, app, window, tab, session):
    """A tmux window after `tab`'s, in `session`'s folder on the tmux host, as the tab right
    after `tab`. tmux's new-window, not iTerm2's "New Tmux Tab": that menu item acts on the
    key window, so iTerm2 would come to the front, and it opens in the profile's folder."""
    tc = await iterm2.async_get_tmux_connection_by_connection_id(conn, tab.tmux_connection_id)
    if not tc:
        raise NewSessionError("This tmux session is no longer connected.")
    pane = await session.async_get_variable("tmuxWindowPane") if session else None
    folder, why = "", "iTerm2 does not say which tmux pane is selected"
    try:
        if pane not in (None, ""):
            folder = (await tc.async_send_command(f"display -p -t %{str(pane).lstrip('%')} '#{{pane_current_path}}'")).strip("\n")
            why = "tmux does not know the pane's folder" if not folder else None
        if CONTROL.search(folder):
            folder, why = "", "the pane's folder name holds control characters"
        command = f"new-window -a -t @{str(tab.tmux_window_id).lstrip('@')}" + (f" -c {tmux_word(folder)}" if folder else "")
        made = "@" + (await tc.async_send_command(command + " -P -F '#{window_id}'")).strip().lstrip("@")
    except iterm2.TmuxException as e:
        raise NewSessionError(f"tmux did not open a window: {e}") from e

    def find():
        return next((t for w in app.terminal_windows for t in w.tabs if t.tmux_connection_id == tab.tmux_connection_id
                     and "@" + str(t.tmux_window_id).lstrip("@") == made), None)
    sid = await settled(find, f"tmux opened window {made}, but iTerm2 did not show it: look for it before pressing + again.", TMUX_TRIES)
    await place_after(app, window.window_id, tab.tab_id, find())
    return sid, f"The new tmux window starts in tmux's default folder: {why}." if why else None


async def place_after(app, window_id, after_id, new):
    """`new` as the tab right after `after_id` in that window. iTerm2 may open a tmux window
    elsewhere (as a window of its own, which closes once its tab moves); a window that closed
    meanwhile leaves the new session where it is."""
    window = app.get_window_by_id(window_id)
    if not window or not new:
        return
    rest = [t for t in window.tabs if t.tab_id != new.tab_id]
    at = next((n + 1 for n, t in enumerate(rest) if t.tab_id == after_id), len(rest))
    order = rest[:at] + [new] + rest[at:]
    if [t.tab_id for t in order] != [t.tab_id for t in window.tabs]:
        await window.async_set_tabs(order)


async def settled(find, failure, tries=SETTLE_TRIES):
    """The session of the tab `find` returns, once iTerm2 reports it. Tabs are snapshots: `find`
    asks the app, which follows iTerm2's changes."""
    for _ in range(tries):
        tab = find()
        if tab and tab.current_session:
            return tab.current_session.session_id
        await asyncio.sleep(SETTLE_GAP)
    raise NewSessionError(failure)


async def profiles(conn):
    """iTerm2's terminal profiles by name, the default one first; browser profiles (such as the
    Files Viewer's) open no terminal and are left out."""
    found = await iterm2.PartialProfile.async_query(conn, properties=["Name", "Guid", "Custom Command"])
    default = await iterm2.PartialProfile.async_get_default(conn)
    names = sorted({p.name for p in found if p.name and p.all_properties.get("Custom Command") != BROWSER}, key=str.lower)
    first = default.name if default and default.name in names else None
    return ([first] if first else []) + [n for n in names if n != first]


async def new_window(conn, app, profile):
    """"New window": a new iTerm2 window with `profile`; its session's id."""
    if profile not in await profiles(conn):
        raise NewSessionError(f"iTerm2 has no profile named {profile!r}.")
    try:
        window = await iterm2.Window.async_create(conn, profile=profile)
    except Exception as e:  # iTerm2's CreateWindowException
        raise NewSessionError(f"iTerm2 did not open a window with the profile {profile}: {e}") from e
    if not window:
        raise NewSessionError("iTerm2 did not open the window.")

    def find():
        w = app.get_window_by_id(window.window_id)
        return w.current_tab if w else None
    return await settled(find, "iTerm2 did not report the new window.")


async def reorder_tabs(app, window_id, session_ids):
    """Dragging rows in the list: that window's tabs in the order of the rows' sessions (a tab
    with several panes goes where its first pane is). Only the window's own tabs are taken:
    iTerm2 would move tabs of other windows into it."""
    window = app.get_window_by_id(window_id)
    if not window:
        raise NewSessionError("This window has closed.")
    own = {t.tab_id: t for t in window.tabs}
    order = []
    for sid in session_ids:
        s = app.get_session_by_id(sid)
        tab = s.tab if s else None
        if tab is None or tab.tab_id not in own:
            raise NewSessionError("The list is out of date: pick up the row again.")
        if own[tab.tab_id] not in order:
            order.append(own[tab.tab_id])
    order += [t for t in window.tabs if t not in order]
    await window.async_set_tabs(order)
