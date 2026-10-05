# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""One metadata graph per reachable tmux server; no pane output or job argv."""
import asyncio
import hashlib
import json
import shlex

import iterm2

from . import procinfo
from .common import LOCAL_HOST, UserError
from .recovery_recipes import ssh_recipe
from .resolve import gateway_target, static_vars

INFO = "#{host}\t#{socket_path}\t#{pid}\t#{session_id}\t#{session_created}\t#{pane_id}\t#{start_time}"
SESSIONS = "#{session_id}\t#{session_created}\t#{session_name}\t#{session_group}"
WINDOWS = "#{session_id}\t#{window_id}\t#{window_index}\t#{window_layout}\t#{pane_id}"
PANES = "#{window_id}\t#{pane_id}\t#{pane_current_path}\t#{pane_current_command}\t#{pane_width}\t#{pane_height}"


async def local_command(argv):
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 3)
    except BaseException:
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        raise UserError("tmux server unavailable")
    return out.decode("utf-8", "strict")


class TmuxGraphs:
    def __init__(self, conn):
        self.conn = conn
        self.servers = {}
        self.connections = {}
        self.warnings = set()
        self.lock = asyncio.Lock()

    async def reader(self, session, control):
        if control:
            tab = session.tab
            tc = await iterm2.async_get_tmux_connection_by_connection_id(self.conn, tab.tmux_connection_id)
            if not tc:
                raise UserError("tmux control connection unavailable")
            target = await gateway_target(tc)
            args = ssh_recipe(["ssh", *target]) if target else []
            if target and args is None:
                raise UserError("Unsupported SSH recovery recipe")
            async def run(argv):
                return await tc.async_send_command(shlex.join(argv))
            # Unlike plain SSH this API has a proved tmux control channel; the
            # remote job command itself is neither saved nor executed.
            return run, args, False if target else True, None
        root, tty = await static_vars(session)
        pid = next((p for p in map(procinfo.foreground_pid, [root, *procinfo.proc_children(root or 0)]) if p), None)
        if not pid or procinfo.proc_name(pid) != "tmux":
            raise UserError("tmux client identity unavailable")
        command = procinfo.tmux_command(pid)
        async def run(argv):
            return await local_command([*command, *argv])
        return run, [], True, tty

    async def connection(self, session, control):
        async with self.lock:
            return await self._connection(session, control)

    async def _connection(self, session, control):
        cached = self.connections.get(session.session_id)
        if cached:
            return cached
        run, args, local, tty = await self.reader(session, control)
        client = []
        if tty:
            rows = await run(["list-clients", "-F", "#{client_tty}\t#{session_id}\t#{client_flags}"])
            matches = [(sid, flags) for device, sid, flags in (row.split("\t") for row in rows.splitlines()) if device == tty]
            if len(matches) != 1:
                raise UserError("tmux client session identity unavailable")
            sid, flags = matches[0]
            if "active-pane" in flags.split(","):
                self.warnings.add("tmux client-local selected pane unavailable; window active pane used")
            # -c sets formatting client, not command target; without -t tmux
            # silently uses another client's session/window.
            client = ["-c", tty, "-t", sid]
        info = await run(["display-message", *client, "-p", INFO])
        host, socket, pid, current_session, created, current_pane, started = info.strip().split("\t")
        if local and host.split(".")[0].lower() != LOCAL_HOST:
            raise UserError("Unverified remote tmux server")
        server_id = f"tmux:{host.split('.')[0].lower()}:{socket}"
        if not local:
            identity = hashlib.sha256(json.dumps([host, args], separators=(",", ":")).encode()).hexdigest()[:16]
            server_id += ":" + identity
        if server_id not in self.servers:
            sessions_text = await run(["list-sessions", "-F", SESSIONS])
            windows_text = await run(["list-windows", "-a", "-F", WINDOWS])
            panes_text = await run(["list-panes", "-a", "-F", PANES])
            if await run(["list-windows", "-a", "-F", WINDOWS]) != windows_text or await run(["list-sessions", "-F", SESSIONS]) != sessions_text:
                raise UserError("tmux layout changed during capture; graph omitted")
            panes = {}
            seen = {}
            for line in panes_text.splitlines():
                wid, pane, cwd, job, width, height = line.split("\t")
                key = wid, pane
                if key in seen:
                    if seen[key] != line:
                        raise UserError("tmux pane changed during capture; graph omitted")
                    continue
                seen[key] = line
                panes.setdefault(wid, []).append({"id": pane, "cwd": cwd or None, "job": job or None,
                                                  "grid": {"width": int(width), "height": int(height)}})
            windows = {}
            for line in windows_text.splitlines():
                sid, wid, index, layout, active = line.split("\t")
                windows.setdefault(sid, []).append({"id": wid, "index": int(index), "layout": layout,
                                                    "active": active, "panes": panes[wid]})
            sessions = []
            for line in sessions_text.splitlines():
                sid, born, name, group = line.split("\t")
                record = {"id": sid, "created": int(born), "name": name, "windows": windows.get(sid, [])}
                if group:
                    record["group"] = group
                sessions.append(record)
            self.servers[server_id] = {"id": server_id, "local": local, "socket": socket, "args": args,
                                       "pid": int(pid), "started": procinfo.proc_start(int(pid)) or 0 if local else int(started),
                                       "sessions": sessions}
        if control:
            # A control tab may belong to a session other than the client's active one.
            pane = await session.async_get_variable("tmuxWindowPane")
            current_pane = f"%{pane}" if not str(pane).startswith("%") else str(pane)
            matches = [s for s in self.servers[server_id]["sessions"] if any(
                p["id"] == current_pane for w in s["windows"] for p in w["panes"])]
            if not matches:
                raise UserError("tmux pane identity unavailable")
            current_session = next((s["id"] for s in matches if s["id"] == current_session), matches[0]["id"])
        value = {"kind": "tmux", "server": server_id, "session": current_session,
                 "pane": current_pane, "control": control}
        self.connections[session.session_id] = value
        return value
