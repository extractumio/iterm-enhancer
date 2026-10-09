# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Human labels for iTerm2 windows, tabs and sessions.

Titles come first: a title the user gave the tab (or the tmux window), else the title the
program set (OSC 0/2, e.g. Claude Code's task name). Only when there is none does the label
fall back to the running program and the directory. The line below names the program, then the
folder, then the other titles: those matter least.
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
# A title that is only a program's name says nothing the program column does not; a pane keeps
# such a title from what ran in it before (a tmux pane titled "ssh" that now runs claude).
PROGRAM_NAMES = SHELLS | set(AGENTS) | {"ssh", "mosh", "tmux", "screen", "sudo", "login"}
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
    """{(tmux connection id, "%pane"): (the program the pane runs, whether tmux names its window
    after it (None: this tmux does not say), the window's name)}. iTerm2 reports a tmux pane's
    job as the gateway's ssh, a pane or window the user named hides Claude Code's title marks,
    and iTerm2 keeps a window's name from when its tab opened, so tmux, which knows, is asked:
    one command per connection for all its panes."""
    found = {}
    for tc in await iterm2.async_get_tmux_connections(app.connection):
        try:
            out = await tc.async_send_command("list-panes -a -F '#{pane_id} #{automatic-rename} #{pane_current_command} #{window_name}'")
        except Exception as e:  # a connection going away: its panes show no program this time
            log(f"web: list-panes on {tc.connection_id} failed: {e}")
            continue
        for line in out.splitlines():
            pane, auto, cmd, name = (line.split(" ", 3) + ["", "", ""])[:4]
            found[(tc.connection_id, pane)] = (cmd.strip(), {"1": True, "0": False}.get(auto), name)
    return found


async def session_item(session, tab, tv, pane_no, panes, tab_no, progs=None):
    v = await _vars(session, SESSION_VARS)
    job = v["processTitle"] or v["jobName"]
    in_tmux = tab.tmux_window_id not in (None, "-1")
    auto = None
    window_name = tv["tmuxWindowName"]
    if in_tmux:      # the pane's own program, not the gateway's ssh
        job, auto, live = (progs or {}).get((tab.tmux_connection_id, "%" + str(v["tmuxWindowPane"]).lstrip("%")), ("", None, ""))
        window_name = live or window_name
    path = tilde(v["path"])
    titles = []                                      # shown after the program and the folder
    if tab.tmux_window_id not in (None, "-1"):
        program_title = v["name"] if v["name"] not in ("", "tmux", window_name) else ""
        # tmux says whether it names the window after its program; an older one does not, and
        # then a name that looks like a command is taken for tmux's own
        user_named = bool(window_name) and (not auto if auto is not None else not AUTO_NAME.fullmatch(window_name))
        title = window_name if user_named else (program_title or window_name or "tmux window")
        if not job and not user_named:               # tmux names a window after its program
            job = window_name
        titles.append(program_title)
        host = ssh_host(v["commandLine"])            # the tmux gateway: ssh … "tmux -CC …"
    else:
        user_title = tv["titleOverride"]
        program_title = v["terminalWindowName"] or v["terminalIconName"]
        title = user_title or program_title or job or v["profileName"] or "Shell"
        titles.append(program_title)
        host = ssh_host(v["commandLine"]) if v["jobName"] == "ssh" else None
    sub = [job] if job and job != title else []
    if path:
        sub.append(path)
    if panes > 1:
        sub.append(f"pane {pane_no} of {panes}")
    sub += [t for t in titles if t and t != title and t not in sub and t.lower() not in PROGRAM_NAMES]
    # The tab's position, as in iTerm's tab bar. Not the tmux window number: iTerm keeps the
    # tmux title from when the tab opened, so after renumbering two tabs could show "3".
    # A tmux pane's own title reaches iTerm2 as the session's name
    raw = (v["name"] if in_tmux else v["terminalWindowName"]) or ""
    agent = agent_of(job, v["commandLine"]) or (window_name if in_tmux and window_name in AGENTS else None) \
        or ("claude" if claude_marked(raw) else None)
    return {"id": session.session_id, "title": bare(title), "sub": [bare(s) for s in sub], "index": str(tab_no) if tab_no else "",
            "host": host,                                  # remote host name, None on this Mac
            "profile": v["profileName"],                   # its dot in the list: one color per profile
            "agent": agent,
            # a shell at its prompt: a tap may move its cursor along the line (not in htop, less, mc)
            "shell": job in SHELLS,
            "state": await session_state(session, raw) if agent else None}   # working, waiting, failed, done


async def window_group(w, number, progs=None):
    """One iTerm2 window: its tabs and panes, all asked for at once."""
    tvs = await asyncio.gather(*(_vars(t, ["titleOverride", "tmuxWindowName", "tmuxWindowTitle"]) for t in w.tabs))
    numbered = len(w.tabs) > 1
    items = await asyncio.gather(*(session_item(s, t, tv, i, len(t.sessions), n if numbered else 0, progs)
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
    progs = await tmux_programs(app) if any(t.tmux_window_id not in (None, "-1") for x in app.terminal_windows for t in x.tabs) else {}
    return list(await asyncio.gather(*(window_group(w, n, progs) for n, w in enumerate(app.terminal_windows, 1))))
