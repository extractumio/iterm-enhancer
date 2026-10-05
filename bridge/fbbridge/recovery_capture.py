# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Complete terminal metadata sweeps, kept separate from the Files follower."""
import asyncio
import json
import os
import subprocess
import time

import iterm2

from . import procinfo
from .common import LOCAL_HOST, UserError, log
from .recovery_recipes import capture_ssh_recipe
from .recovery_liveness import session_state
from .recovery_tmux_capture import TmuxGraphs
from .resolve import resolve, static_vars


def layout(node):
    if isinstance(node, iterm2.Session):
        return {"pane": node.session_id}
    return {"vertical": node.vertical, "children": [layout(c) for c in node.children]}


def signature(windows):
    return [(w.window_id, [(t.tab_id, layout(t.root), sorted(s.session_id for s in t.all_sessions))
                          for t in w.tabs]) for w in windows]


def canonical(node, keep=None):
    if "pane" in node:
        return node if keep is None or node["pane"] in keep else None
    children = [c for n in node["children"] if (c := canonical(n, keep)) is not None]
    flat = []
    for c in children:
        flat.extend(c["children"] if "children" in c and c["vertical"] == node["vertical"] else [c])
    return None if not flat else flat[0] if len(flat) == 1 else {"vertical": node["vertical"], "children": flat}


def epoch(process=None):
    process = process or procinfo.iterm_process()
    if not process:
        # A script run outside AutoLaunch still identifies the actual iTerm2 process,
        # not its own Python PID. Only process names/PIDs are read, never argv/env.
        rows = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=2).stdout.splitlines()
        candidates = [int(row.split(None, 1)[0]) for row in rows if row.rstrip().endswith("/Contents/MacOS/iTerm2")]
        if len(candidates) != 1:
            raise UserError("iTerm2 run identity unavailable")
        process = candidates[0], procinfo.proc_start(candidates[0])
    boot = subprocess.run(["sysctl", "-n", "kern.bootsessionuuid"], capture_output=True, text=True, timeout=2).stdout.strip()
    if not boot or not process[1]:
        raise UserError("iTerm2 run identity unavailable")
    return f"{boot}:{process[0]}:{process[1]}"


class Capture:
    def __init__(self, conn, app, windows, backend, window_ids=None):
        self.conn, self.app, self.windows, self.backend = conn, app, windows, backend
        self.epoch = None
        self.previous = {}
        self.ssh_observations = {}
        self.lock = asyncio.Lock()
        self.restoring = False
        self.ready = asyncio.Event()
        self.startup_settled = asyncio.Event()
        self.tmux = None
        self.window_ids = window_ids  # integration fixtures restrict capture to owned windows

    async def call(self, method, path, body=None):
        return await asyncio.to_thread(self.backend.query, method, path, body)

    async def profile(self, session):
        response = await iterm2.rpc.async_get_profile(self.conn, session.session_id, keys=["Guid", "Custom Command"])
        return {p.key: json.loads(p.json_value) for p in response.get_profile_response.properties}

    async def pane(self, session, control):
        sid = session.session_id
        r = await resolve(self.conn, session)
        try:
            profile = await self.profile(session)
        except Exception:
            profile = {}
        cwd = r.get("path") if r.get("remote_host") else r.get("cwd")
        connection = {"kind": "shell"}
        if control or r["mode"] == "tmux":
            try:
                connection = await self.tmux.connection(session, control)
            except Exception:
                connection = {"kind": "unsupported", "reason": "tmux topology or connection recipe is unavailable"}
        elif r["mode"] == "remote":
            host = await session.async_get_variable("hostname")
            cwd = await session.async_get_variable("path") if host and host.split(".")[0].lower() != LOCAL_HOST else None
            root, tty = await static_vars(session)
            foreground = next((p for p in map(procinfo.foreground_pid, [root, *procinfo.proc_children(root or 0)]) if p), None)
            ssh = foreground if foreground and procinfo.proc_name(foreground) == "ssh" else procinfo.find_descendant(foreground, "ssh") if foreground else None
            args = capture_ssh_recipe(procinfo.proc_argv(ssh)) if ssh else None
            connection = {"kind": "ssh", "args": args} if args else {"kind": "unsupported", "reason": "Unsupported SSH wrapper or remote command"}
            identity = (ssh, procinfo.proc_start(ssh)) if ssh else None
            signature = (host, cwd)
            prior = self.ssh_observations.get(sid)
            # A new SSH process may inherit the previous connection's OSC values.
            # Trust direct host matches initially; aliases need a fresh observation.
            destination = args[-1].rsplit("@", 1)[-1].strip("[]") if args else ""
            trusted = bool(cwd) and (host.split(".")[0].lower() == destination.split(".")[0].lower() if host else False)
            if prior:
                trusted = bool(cwd) and (prior[2] if prior[0] == identity else False)
                trusted |= bool(cwd) and signature != prior[1]
            self.ssh_observations[sid] = (identity, signature, trusted)
            if not trusted:
                cwd = None
        elif profile.get("Custom Command") == "Browser":
            connection = {"kind": "unsupported", "reason": "Browser panes are not terminal sessions"}
        last = self.previous.get(sid, {})
        observer = getattr(self, "lifecycle", None)
        source = observer.recipe(sid) if observer else None
        if source and source["connection"]["kind"] == "ssh" and r["mode"] != "remote":
            # A generated connection may still be authenticating. Keep its validated
            # recipe without mistaking the local startup shell for its destination.
            connection = source["connection"]
            cwd = None
        if last.get("connection") != connection:
            last = {}
        status, observed = ("known", int(time.time())) if cwd else ("stale", last.get("observed", 0)) if last.get("cwd") else ("unknown", 0)
        if not cwd:
            cwd = last.get("cwd")
        grid = session.grid_size
        result = {"id": sid, "profile": profile.get("Guid"), "cwd": cwd, "cwd_status": status, "observed": observed,
                  "job": r.get("job"), "grid": {"width": grid.width, "height": grid.height}, "connection": connection}
        if observer and source and source["connection"]["kind"] == "ssh":
            if source.get("cwd") and source["cwd_status"] == "known" and status != "known":
                observer.connecting.add(sid)  # auth must not erase a previously verified remote directory
            else:
                observer.connecting.discard(sid)
        self.previous[sid] = result
        return result

    async def snapshot(self):
        response = await iterm2.rpc.async_list_sessions(self.conn)
        windows = [iterm2.Window.create_from_proto(self.conn, w) for w in response.list_sessions_response.windows]
        if any(w is None for w in windows):
            raise UserError("Terminal checkpoint incomplete; previous generation kept")
        windows = [w for w in windows if w.window_id != self.windows.viewer_id and
                   (self.window_ids is None or w.window_id in self.window_ids)]
        before = signature(windows)
        captured, warnings = [], []
        self.tmux = TmuxGraphs(self.conn)
        limit = asyncio.Semaphore(4)

        def unavailable(s):
            observer = getattr(self, "lifecycle", None)
            source = observer.recipe(s.session_id) if observer else None
            if source and source["connection"]["kind"] == "ssh" and source.get("cwd") and source["cwd_status"] == "known":
                observer.connecting.add(s.session_id)
            last = self.previous.get(s.session_id)
            if last:
                return {**last, "cwd_status": "stale"}
            return {"id": s.session_id, "profile": None, "cwd": None, "cwd_status": "unknown", "observed": 0,
                    "job": None, "grid": {"width": s.grid_size.width, "height": s.grid_size.height},
                    "connection": {"kind": "unsupported", "reason": "Terminal metadata or liveness unavailable"}}

        async def pane(s, control, alive):
            async with limit:
                if not alive:
                    return unavailable(s)  # A reused PID cannot supply another process's cwd or argv.
                try:
                    return await asyncio.wait_for(self.pane(s, control), 5)
                except Exception:
                    return unavailable(s)

        for w in windows:
            tabs = []
            for t in w.tabs:
                async def state(s):
                    async with limit:
                        try:
                            return await asyncio.wait_for(session_state(s), 5)
                        except Exception:
                            return "unknown"
                states = await asyncio.gather(*(state(s) for s in t.all_sessions))
                sessions = [s for s, alive in zip(t.all_sessions, states) if alive != "ended"]
                if not sessions:
                    continue
                known = {s.session_id for s, alive in zip(t.all_sessions, states) if alive == "live"}
                panes = await asyncio.gather(*(pane(s, bool(t.tmux_connection_id), s.session_id in known) for s in sessions))
                # all_sessions includes minimized panes; the public visible split tree
                # does not. Preserve them as explicit additional leaves for recovery.
                ids = {s.session_id for s in sessions}
                root = canonical(layout(t.root), ids)
                hidden = [s.session_id for s in sessions if s not in t.sessions]
                if hidden:
                    children = ([root] if root else []) + [{"pane": sid} for sid in hidden]
                    root = children[0] if len(children) == 1 else {"vertical": True, "children": children}
                    warnings.append("Minimized panes are restored as visible additional splits")
                tabs.append({"id": t.tab_id, "control": bool(t.tmux_connection_id),
                             "active": t.current_session.session_id if t.current_session and t.current_session.session_id in ids else panes[0]["id"],
                             "tree": root, "panes": panes})
            if not tabs:
                continue
            frame = await asyncio.wait_for(w.async_get_frame(), 5)
            captured.append({"id": w.window_id, "frame": frame.dict, "fullscreen": await w.async_get_fullscreen(),
                             "active": w.current_tab.tab_id if w.current_tab and any(t["id"] == w.current_tab.tab_id for t in tabs) else tabs[0]["id"], "tabs": tabs})
        after = await iterm2.rpc.async_list_sessions(self.conn)
        latest = [iterm2.Window.create_from_proto(self.conn, w) for w in after.list_sessions_response.windows]
        if any(w is None for w in latest):
            raise UserError("Terminal checkpoint incomplete; previous generation kept")
        latest = [w for w in latest if w.window_id != self.windows.viewer_id and
                  (self.window_ids is None or w.window_id in self.window_ids)]
        if signature(latest) != before:
            raise UserError("Terminal layout changed during capture; previous generation kept")
        live = {p["id"] for w in captured for t in w["tabs"] for p in t["panes"]}
        self.previous = {k: v for k, v in self.previous.items() if k in live}
        self.ssh_observations = {k: v for k, v in self.ssh_observations.items() if k in live}
        return {"version": 1, "epoch": self.epoch, "captured": int(time.time()), "windows": captured,
                "servers": list(self.tmux.servers.values()), "warnings": sorted(set(warnings) | self.tmux.warnings)}

    async def save(self, force=False):
        if not self.ready.is_set():
            raise UserError("Startup recovery pending; checkpoint capture paused")
        async with self.lock:
            if self.restoring:
                raise UserError("Terminal recovery is running; capture resumes when it finishes")
            if self.epoch is None:
                self.epoch = await asyncio.to_thread(epoch)
            snapshot = await asyncio.wait_for(self.snapshot(), 30)
            observer = getattr(self, "lifecycle", None)
            if observer:
                await asyncio.wait_for(observer.poll(), 4)
                if observer.failed:
                    raise UserError("Recovered connection exited unsuccessfully; previous checkpoint preserved for Retry")
                if observer.connecting:
                    raise UserError("Recovered SSH directory awaiting confirmation; previous checkpoint preserved")
                if not observer.operational() or observer.unconfirmed():
                    raise UserError("Session closures awaiting confirmation; previous checkpoint preserved")
            return await self.call("POST", "/internal/recovery/capture", {"snapshot": snapshot, "force": force})

    async def follow(self):
        await self.ready.wait()
        while True:
            try:
                status = await self.call("GET", "/internal/recovery")
                if self.epoch is None:
                    self.epoch = await asyncio.to_thread(epoch)
                if status["enabled"] and not self.restoring:
                    await self.save()
            except Exception as e:
                message = str(e) if isinstance(e, UserError) else f"Terminal checkpoint unavailable ({type(e).__name__})"
                log(message)
                try:
                    await self.call("POST", "/internal/recovery/error", {"message": message})
                except Exception:
                    pass
            await asyncio.sleep(5)
