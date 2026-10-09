# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Human labels for iTerm2 windows, tabs and sessions.

Titles come first: a title the user gave the tab (or the tmux window), else the title the
program set (OSC 0/2, e.g. Claude Code's task name). Only when there is none does the label
fall back to the running program and the directory.
"""
import asyncio
import re
import shlex

import iterm2

from .. import sshargs
from ..common import SHELLS, log
from ..viewer_profile import VIEWER_PROFILE
from .agentstate import claude_marked, session_state
from .newsession import profile_of

AUTO_NAME = re.compile(r"[\w.+@-]+")   # tmux names a window after its command ("claude", "zsh")
AGENTS = ("claude", "codex")            # coding agents the list marks with their own icon
# Status marks programs put in front of a title (Claude Code's ✳ ✻ ✶ ◐ ⏺, braille spinners)
STATUS = re.compile("^[\u2800-\u28ff\u25c9-\u25d3\u2722-\u273d\u23f3\u23fa\u231b\u00b7*\ufe0e\ufe0f\\s]+")


def bare(title):
    """A title without the status marks in front of it; as it is when nothing else is left."""
    return STATUS.sub("", title) or title


def agent_of(job, command_line):
    """"claude" or "codex" when the pane runs one (its program, or the command it was started with)."""
    words = (command_line or "").split()
    first = words[0].rsplit("/", 1)[-1] if words else ""
    name = (job or "").strip().lower()
    return next((a for a in AGENTS if name == a or first == a), None)


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
                "commandLine", "path", "profileName", "tmuxWindowPane"]


async def tmux_programs(app):
    """{(tmux connection id, "%pane"): the program the pane runs}. iTerm2 reports a tmux pane's
    job as the gateway's ssh, and a pane or window the user named hides Claude Code's title
    marks, so tmux, which knows, is asked: one command per connection for all its panes."""
    found = {}
    for tc in await iterm2.async_get_tmux_connections(app.connection):
        try:
            out = await tc.async_send_command("list-panes -a -F '#{pane_id} #{pane_current_command}'")
        except Exception as e:  # a connection going away: its panes show no program this time
            log(f"web: list-panes on {tc.connection_id} failed: {e}")
            continue
        for line in out.splitlines():
            pane, _, cmd = line.partition(" ")
            found[(tc.connection_id, pane)] = cmd.strip()
    return found


async def session_item(session, tab, tv, focused_id, pane_no, panes, tab_no, progs=None):
    v = await _vars(session, SESSION_VARS)
    job = v["processTitle"] or v["jobName"]
    in_tmux = tab.tmux_window_id not in (None, "-1")
    if in_tmux:      # the pane's own program, not the gateway's ssh
        job = (progs or {}).get((tab.tmux_connection_id, "%" + str(v["tmuxWindowPane"]).lstrip("%")), "")
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
        if job and job not in sub and job != title:
            sub.append(job)
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
    # A tmux pane's own title reaches iTerm2 as the session's name
    raw = (v["name"] if in_tmux else v["terminalWindowName"]) or ""
    agent = agent_of(job, v["commandLine"]) or (tv["tmuxWindowName"] if in_tmux and tv["tmuxWindowName"] in AGENTS else None) \
        or ("claude" if claude_marked(raw) else None)
    return {"id": session.session_id, "title": bare(title), "sub": [bare(s) for s in sub], "index": str(tab_no) if tab_no else "",
            "host": host,                                  # remote host name, None on this Mac
            "profile": v["profileName"],                   # its dot in the list: one color per profile
            "agent": agent,
            # a shell at its prompt: a tap may move its cursor along the line (not in htop, less, mc)
            "shell": job in SHELLS,
            "state": await session_state(session, raw) if agent else None,   # working, waiting, failed, done
            "focused": session.session_id == focused_id}


async def window_group(w, number, focused, progs=None):
    """One iTerm2 window: its tabs and panes, all asked for at once."""
    tvs = await asyncio.gather(*(_vars(t, ["titleOverride", "tmuxWindowName", "tmuxWindowTitle"]) for t in w.tabs))
    numbered = len(w.tabs) > 1
    items = await asyncio.gather(*(session_item(s, t, tv, focused, i, len(t.sessions), n if numbered else 0, progs)
                                   for n, (t, tv) in enumerate(zip(w.tabs, tvs), 1)
                                   for i, s in enumerate(t.sessions, 1)))
    tmux = next(((t, tv) for t, tv in zip(w.tabs, tvs) if t.tmux_window_id not in (None, "-1")), None)
    if tmux:
        t, tv = tmux
        host = next((it["host"] for it in items if it["host"]), None)   # every pane of it shares the gateway
        return {"kind": "tmux", "label": "tmux", "session": tv["tmuxWindowTitle"].split(":")[0],
                "where": "" if host else "on this Mac", "host": host, "items": items,
                "wid": w.window_id, "new": "New tmux tab", "pool": t.tmux_connection_id}
    wn = await w.async_get_variable("number")
    profile = await profile_of(w)
    # "Merge windows" (merge.py) gathers the windows of one pool; the Files viewer stays alone
    viewer = bool(items) and all(it["profile"] == VIEWER_PROFILE for it in items)
    return {"kind": "window", "label": f"Window {wn or number}", "where": "", "host": None, "items": items,
            "wid": w.window_id, "new": f"New tab ({profile})" if profile else "New tab", "pool": None if viewer else ""}


async def layout_of(app):
    """[{kind, label, where, host, items: [...]}] in iTerm's window order."""
    w = app.current_terminal_window
    focused = w.current_tab.current_session.session_id if w and w.current_tab and w.current_tab.current_session else None
    progs = await tmux_programs(app) if any(t.tmux_window_id not in (None, "-1") for x in app.terminal_windows for t in x.tabs) else {}
    return list(await asyncio.gather(*(window_group(w, n, focused, progs) for n, w in enumerate(app.terminal_windows, 1))))
