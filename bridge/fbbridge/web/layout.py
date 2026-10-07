# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Human labels for iTerm2 windows, tabs and sessions.

Titles come first: a title the user gave the tab (or the tmux window), else the title the
program set (OSC 0/2, e.g. Claude Code's task name). Only when there is none does the label
fall back to the running program and the directory.
"""
import asyncio
import re
import shlex

from .. import sshargs

AUTO_NAME = re.compile(r"[\w.+@-]+")   # tmux names a window after its command ("claude", "zsh")


def tilde(path):
    return re.sub(r"^/(Users|home)/[^/]+", "~", path or "")


def ssh_host(command_line):
    """The host an `ssh ...` command line reaches, without user@ (sshargs reads it as the bridge does)."""
    try:
        t = sshargs.target(shlex.split(command_line or ""))
    except ValueError:
        return None
    return t[-1].rsplit("@", 1)[-1] if t else None


async def _vars(obj, names):
    values = await asyncio.gather(*(obj.async_get_variable(n) for n in names))
    return {n: (v or "") if not isinstance(v, (int, float)) else v for n, v in zip(names, values)}


SESSION_VARS = ["name", "terminalWindowName", "terminalIconName", "jobName", "processTitle",
                "commandLine", "path", "profileName"]


async def session_item(session, tab, tv, focused_id, pane_no, panes, tab_no):
    v = await _vars(session, SESSION_VARS)
    job = v["processTitle"] or v["jobName"]
    path = tilde(v["path"])
    sub = []
    if tab.tmux_window_id not in (None, "-1"):
        window_name = tv["tmuxWindowName"]
        program_title = v["name"] if v["name"] not in ("", "tmux", window_name) else ""
        user_named = bool(window_name) and not AUTO_NAME.fullmatch(window_name)
        title = window_name if user_named else (program_title or window_name or "tmux window")
        if program_title and program_title != title:
            sub.append(program_title)
        if window_name and window_name != title and not user_named:
            sub.append(window_name)
        host = ssh_host(v["commandLine"])            # the tmux gateway: ssh … "tmux -CC …"
    else:
        user_title = tv["titleOverride"]
        program_title = v["terminalWindowName"] or v["terminalIconName"]
        title = user_title or program_title or job or v["profileName"] or "Shell"
        if program_title and program_title != title:
            sub.append(program_title)
        if job and job != title:
            sub.append(job)
        host = ssh_host(v["commandLine"]) if v["jobName"] == "ssh" else None
    if path:
        sub.append(path)
    if panes > 1:
        sub.insert(0, f"pane {pane_no} of {panes}")
    # The tab's position, as in iTerm's tab bar. Not the tmux window number: iTerm keeps the
    # tmux title from when the tab opened, so after renumbering two tabs could show "3".
    return {"id": session.session_id, "title": title, "sub": sub, "index": str(tab_no) if tab_no else "",
            "host": host,                                  # remote host name, None on this Mac
            "focused": session.session_id == focused_id}


async def window_group(w, number, focused):
    """One iTerm2 window: its tabs and panes, all asked for at once."""
    tvs = await asyncio.gather(*(_vars(t, ["titleOverride", "tmuxWindowName", "tmuxWindowTitle"]) for t in w.tabs))
    numbered = len(w.tabs) > 1
    items = await asyncio.gather(*(session_item(s, t, tv, focused, i, len(t.sessions), n if numbered else 0)
                                   for n, (t, tv) in enumerate(zip(w.tabs, tvs), 1)
                                   for i, s in enumerate(t.sessions, 1)))
    tmux = next(((t, tv) for t, tv in zip(w.tabs, tvs) if t.tmux_window_id not in (None, "-1")), None)
    if tmux:
        t, tv = tmux
        host = next((it["host"] for it in items if it["host"]), None)   # every pane of it shares the gateway
        return {"kind": "tmux", "label": f"tmux {tv['tmuxWindowTitle'].split(':')[0]}",
                "where": "" if host else "on this Mac", "host": host, "items": items}
    wn = await w.async_get_variable("number")
    return {"kind": "window", "label": f"Window {wn or number}", "where": "", "host": None, "items": items}


async def layout_of(app):
    """[{kind, label, where, host, items: [...]}] in iTerm's window order."""
    w = app.current_terminal_window
    focused = w.current_tab.current_session.session_id if w and w.current_tab and w.current_tab.current_session else None
    return list(await asyncio.gather(*(window_group(w, n, focused) for n, w in enumerate(app.terminal_windows, 1))))
