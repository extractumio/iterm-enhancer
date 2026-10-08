# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""New sessions from the web app's session list (AC-52): [+] on a group opens a new tab in that
iTerm2 window, with the profile of the window's current session or, in a tmux -CC window, a
new tmux window as a tab there; "New window" opens a window with a profile the user picks."""
import asyncio

import iterm2

from ..viewer_profile import BROWSER

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


async def new_session(conn, app, window_id):
    """[+]: the new session's id."""
    window = app.get_window_by_id(window_id)
    if not window:
        raise NewSessionError("This window has closed.")
    tmux = tmux_tab(window)
    if tmux:
        if not await iterm2.async_get_tmux_connection_by_connection_id(conn, tmux.tmux_connection_id):
            raise NewSessionError("This tmux session is no longer connected.")
        before = {t.tab_id for w in app.terminal_windows for t in w.tabs}
        await new_tmux_tab(app, conn, window)
        tries = TMUX_TRIES                   # through ssh a tmux tab takes longer than a local one

        def find():
            return next((t for w in app.terminal_windows for t in w.tabs
                         if t.tab_id not in before and t.tmux_connection_id == tmux.tmux_connection_id), None)
    else:
        profile = await profile_of(window)
        try:
            made = await window.async_create_tab(profile=profile or None)
        except Exception as e:  # iTerm2's CreateTabException: a profile renamed or deleted
            raise NewSessionError(f"iTerm2 did not open a tab with the profile {profile or 'Default'}: {e}") from e

        tries = SETTLE_TRIES

        def find():
            return (app.get_tab_by_id(made.tab_id) or made) if made else None
    return await settled(find, "iTerm2 did not report the new session.", tries)


async def settled(find, failure, tries=SETTLE_TRIES):
    """The session of the tab `find` returns, once iTerm2 reports it. Tabs are snapshots: `find`
    asks the app, which follows iTerm2's changes."""
    for _ in range(tries):
        tab = find()
        if tab and tab.current_session:
            return tab.current_session.session_id
        await asyncio.sleep(SETTLE_GAP)
    raise NewSessionError(failure)


async def new_tmux_tab(app, conn, window):
    """iTerm2's "New Tmux Tab" for `window`. The API's create_tab opens a local shell tab in a
    tmux window, and tmux's own new-window opens as iTerm2's preference says (often a window of
    its own); the menu item acts on the key window, so that window is made key for it and the
    one that was key before is given back."""
    was = app.current_terminal_window
    await app.async_activate()
    await window.async_activate()
    await asyncio.sleep(0.3)                 # iTerm2 makes the window key asynchronously
    try:
        await iterm2.MainMenu.async_select_menu_item(conn, "tmux.New Tmux Tab")
    except Exception as e:  # iTerm2's MenuItemException: the item is disabled
        raise NewSessionError(f"iTerm2 did not open a tmux tab: {e}") from e
    finally:
        if was and was.window_id != window.window_id and app.get_window_by_id(was.window_id):
            await asyncio.sleep(0.3)
            await was.async_activate()


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
