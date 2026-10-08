# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The pencil in the web app's header (AC-52): one name for a pane's iTerm2 tab and session,
and for a tmux pane its tmux window and pane title. An empty name gives them back to
iTerm2 and tmux."""
import re

import iterm2

MAX_NAME = 100
# control characters (C0, C1), line and paragraph separators, and bidi overrides that reorder text
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


class RenameError(Exception):
    """Why the name was not set, for the page's status line."""


def checked(name):
    if not isinstance(name, str):
        raise RenameError("A name is text.")
    name = name.strip()
    if CONTROL.search(name):
        raise RenameError("A name cannot hold control characters, line breaks or direction marks.")
    if len(name) > MAX_NAME:
        raise RenameError(f"A name is at most {MAX_NAME} characters.")
    return name


def literal(name):
    """iTerm2 reads titles and names as interpolated strings: a backslash starts \\(…)."""
    return name.replace("\\", "\\\\")


def tmux_word(text):
    """One argument for a tmux command line, taken literally: double quotes, with the
    characters tmux would read inside them ($ for variables, \\ and ") escaped, and # doubled:
    rename-window and select-pane -T expand formats, and #(…) would run a command."""
    return '"' + re.sub(r'([\\"$])', r"\\\1", text).replace("#", "##") + '"'


async def rename(conn, session, name):
    """Name `session` (an iTerm2 Session); "" returns its names to automatic."""
    name = checked(name)
    tab = session.tab
    if tab and tab.tmux_window_id not in (None, "-1"):
        tc = await iterm2.async_get_tmux_connection_by_connection_id(conn, tab.tmux_connection_id)
        if not tc:
            raise RenameError("This tmux session is no longer connected.")
        pane = await session.async_get_variable("tmuxWindowPane")
        if pane in (None, ""):
            raise RenameError("iTerm2 does not say which tmux pane this is.")
        window = "@" + str(tab.tmux_window_id).lstrip("@")
        # The tab shows the tmux window's name, so iTerm2's own tab title is left alone (setting
        # it on a tmux tab may rename the window again). Clearing gives the window back to the
        # user's automatic-rename setting (-u), not "on".
        if name:
            await tc.async_send_command(f"rename-window -t {window} {tmux_word(name)}")
        else:
            await tc.async_send_command(f"set-window-option -u -t {window} automatic-rename")
        await tc.async_send_command(f"select-pane -t %{str(pane).lstrip('%')} -T {tmux_word(name)}")
    elif tab:
        await tab.async_set_title(literal(name))          # "" is iTerm2's automatic title
    # A session's own default name is its profile's
    await session.async_set_name(literal(name or await session.async_get_variable("profileName") or ""))
