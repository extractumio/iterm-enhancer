# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Which directory does a terminal session show: plain shell, tmux, tmux -CC, remote."""
import asyncio
import subprocess

import iterm2

from .common import LOCAL_HOST, REMOTE_JOBS, SHELLS
from .procinfo import foreground_pid, proc_children, proc_cwd, proc_name, shell_of, tmux_command

_static = {}


async def static_vars(session):
    """pid and tty never change for a session: read them once."""
    if session.session_id not in _static:
        _static[session.session_id] = (await session.async_get_variable("pid"), await session.async_get_variable("tty"))
    return _static[session.session_id]


TMUX_FMT = "#{host}\t#{socket_path}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}"


def tmux_result(mode, out):
    host, sock, pane, path, cmd = (out.strip().split("\t") + [""] * 5)[:5]
    key = f"tmux:{host.split('.')[0].lower()}:{sock}:{pane}"
    if host.split(".")[0].lower() != LOCAL_HOST:
        return {"mode": "remote", "key": key, "cwd": None, "note": f"remote host {host}", "job": cmd, "busy": True}
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
        return tmux_result("tmux -CC", out)

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
            "ansi": [color(f"ansi_{i}") for i in range(16)],
            "font": font or None, "size": float(size) if size.replace(".", "", 1).isdigit() else None,
            "dark": dark}
