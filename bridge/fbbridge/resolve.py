# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Which directory does a terminal session show: plain shell, tmux, tmux -CC, remote."""
import asyncio
import subprocess

import iterm2

from . import agentctl
from .common import LOCAL_HOST, REMOTE_JOBS, SHELLS
from .procinfo import find_descendant, foreground_pid, proc_argv, proc_children, proc_cwd, proc_name, shell_of, tmux_command
from .sshargs import key as ssh_key
from .sshargs import target as ssh_target

_static = {}


async def gateway_target(tc):
    """The ssh arguments of a tmux -CC connection's gateway session (AC-38): the `ssh`
    process below it (tms, autossh and scripts exec or start one), read exactly. iTerm2
    can reconnect in the same session, so the process is read again on every lookup."""
    s = tc.owning_session
    pid = await s.async_get_variable("pid") if s else None
    ssh = find_descendant(pid, "ssh") if pid else None
    return ssh_target(proc_argv(ssh)) if ssh else None


async def open_hosts(conn):
    """{key: ssh arguments} of the enabled hosts that have an open tmux -CC window, focused
    or not, so their helpers follow the Mac's build without waiting for focus (AC-42)."""
    found = {}
    for tc in await iterm2.async_get_tmux_connections(conn):
        target = await gateway_target(tc)
        if target and agentctl.entry(ssh_key(target)):
            found[ssh_key(target)] = target
    return found


async def static_vars(session):
    """pid and tty never change for a session: read them once."""
    if session.session_id not in _static:
        _static[session.session_id] = (await session.async_get_variable("pid"), await session.async_get_variable("tty"))
    return _static[session.session_id]


TMUX_FMT = "#{host}\t#{socket_path}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}"


def tmux_result(mode, out, via_ssh=False, destination=None):
    """A tmux pane from `display -p TMUX_FMT`. Remote when its tmux -CC gateway runs ssh,
    whatever the server calls itself (a host must not pose as this Mac, AC-37), or when
    the server's host name is not this Mac's."""
    fields = out.removesuffix("\n").split("\t", 3)
    host, sock, pane, rest = (fields + [""] * 4)[:4]
    path, sep, cmd = rest.rpartition("\t")
    if not sep:
        path, cmd = rest, ""
    key = f"tmux-remote:{destination}:{sock}:{pane}" if destination else f"tmux:{host.split('.')[0].lower()}:{sock}:{pane}"
    if via_ssh or host.split(".")[0].lower() != LOCAL_HOST:  # its files are reached through an agent, if any (AC-37)
        return {"mode": "remote", "key": key, "cwd": None, "note": f"remote host {host}", "job": cmd, "busy": True,
                "remote_host": host.split(".")[0].lower(), "path": path or None, "idle": cmd in SHELLS}
    return {"mode": mode, "key": key, "cwd": path or None, "note": f"pane {pane}", "job": cmd,
            "busy": cmd not in SHELLS}


async def resolve(conn, session):
    """dict(mode, key, cwd, note, job, busy) for an iTerm2 session. `key` identifies the
    terminal pane: the iTerm2 session, or the tmux pane shown in it."""
    v = session.async_get_variable
    if await v("tmuxRole") == "client":  # tmux -CC pane
        pane = await v("tmuxWindowPane")
        tab = session.tab
        tc = None
        if tab is not None:
            for c in await iterm2.async_get_tmux_connections(conn):
                if c.connection_id == tab.tmux_connection_id:
                    tc = c
        if tc is None or pane is None:
            return {"mode": "tmux -CC", "key": session.session_id, "cwd": None, "note": "tmux connection not found"}
        out = await tc.async_send_command(f"display -p -t %{pane} '{TMUX_FMT}'")
        target = await gateway_target(tc)
        r = tmux_result("tmux -CC", out, via_ssh=bool(target), destination=ssh_key(target) if target else None)
        if r["mode"] == "remote":
            r.update(ssh=target, remote_key=ssh_key(target) if target else None)
        return r

    root_pid, tty = await static_vars(session)
    # login is root-owned (its info reads as zeros): fall back to the shells it started
    job_pid = next((fg for fg in map(foreground_pid, [root_pid, *proc_children(root_pid or 0)]) if fg), None) \
        or await v("jobPid")
    job = proc_name(job_pid) if job_pid else None

    if job == "tmux" and tty:  # plain tmux: ask the server which pane this client shows
        proc = await asyncio.create_subprocess_exec(*tmux_command(job_pid), "display", "-c", tty, "-p", TMUX_FMT,
                                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 2)
        except asyncio.TimeoutError:
            proc.kill()
            out, err = b"", b"tmux did not answer"
        if proc.returncode == 0 and out.strip():
            return tmux_result("tmux", out.decode(errors="replace"))
        return {"mode": "tmux", "key": session.session_id, "cwd": None, "note": err.decode(errors="replace").strip()[:200]}

    if job in REMOTE_JOBS:
        return {"mode": "remote", "key": session.session_id, "cwd": None, "note": f"{job} session", "job": job, "busy": True}

    sh = shell_of(job_pid, root_pid)
    sh_name = proc_name(sh) if sh else None
    return {"mode": sh_name or "shell", "key": session.session_id, "cwd": proc_cwd(sh) if sh else None,
            "note": f"pid {sh}" + (f", running {job}" if job and job != sh_name else ""),
            "job": job, "busy": bool(job) and job not in SHELLS}


def _hex(c):
    return "#%02x%02x%02x" % (round(c.red), round(c.green), round(c.blue)) if c else None


async def theme_of(app, session):
    p = await session.async_get_profile()
    dark = "dark" in str(await app.async_get_variable("effectiveTheme") or "dark")
    variant = ("_dark" if dark else "_light") if p.use_separate_colors_for_light_and_dark_mode else ""

    def color(name):
        try:
            return _hex(getattr(p, f"{name}_color{variant}"))
        except AttributeError:
            return _hex(getattr(p, f"{name}_color"))

    font, _, size = (p.normal_font or "").rpartition(" ")
    return {"bg": color("background"), "fg": color("foreground"), "sel": color("selection"),
            "selfg": color("selected_text"), "cursor": color("cursor"), "link": color("link"),
            # the web mirror (AC-52) draws the terminal itself: bold text and the cursor's text too
            "bold": color("bold"), "useBold": bool(p.use_bold_color), "cursorText": color("cursor_text"),
            "ansi": [color(f"ansi_{i}") for i in range(16)],
            "font": font or None, "size": float(size) if size.replace(".", "", 1).isdigit() else None,
            "dark": dark}
