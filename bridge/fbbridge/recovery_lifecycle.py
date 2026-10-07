# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Confirm ordinary exits and GUI closures while the same iTerm2 process responds."""
import asyncio
import time

import iterm2

from . import procinfo
from .common import UserError, log
from .lifecycle import connection_closed
from .recovery_capture import epoch
from .recovery_exit import ExitWatch, root_identity
from .recovery_liveness import session_state

GRACE = 1.5
GAP = 5


class Lifecycle:
    def __init__(self, capture, watch=None):
        self.capture = capture
        self.watch = watch or ExitWatch()
        self.process = None
        self.known = {}
        self.identities = {}
        self.controls = set()
        self.recipes = {}
        self.failed = set()
        self.connecting = set()
        self.live = set()
        self.absent = {}
        self.exits = {}
        self.pending = {}
        self.last = None
        self.lock = asyncio.Lock()

    def operational(self):
        return bool(self.process and procinfo.proc_start(self.process[0]) == self.process[1] and
                    not connection_closed(self.capture.conn.websocket))

    async def inventory(self):
        response = await iterm2.rpc.async_list_sessions(self.capture.conn)
        model = response.list_sessions_response
        windows = [iterm2.Window.create_from_proto(self.capture.conn, w) for w in model.windows]
        if any(w is None for w in windows):
            raise UserError("Session closure inventory incomplete; previous recovery state preserved")
        windows = [w for w in windows if w.window_id != self.capture.windows.viewer_id and
                   (self.capture.window_ids is None or w.window_id in self.capture.window_ids)]
        sessions = [s for w in windows for t in w.tabs for s in t.all_sessions]
        if self.capture.window_ids is None:
            sessions += [iterm2.Session(self.capture.conn, None, s) for s in model.buried_sessions]
        return {s.session_id: s for s in sessions}

    def queue(self, sid, revive):
        if not revive and self.superseded(sid):
            self.pending.pop(sid, None)
            return
        marker = self.known.get(sid)
        self.pending[sid] = {"epoch": self.capture.epoch, "session": sid,
                             "marker": marker, "revive": revive}

    def remember(self, marker, pane):
        if pane:
            self.recipes[marker] = pane

    async def source(self, marker):
        if not isinstance(marker, str) or not marker.startswith("iterm-enhancer Restore "):
            return None
        if marker in self.recipes:
            return self.recipes[marker]
        selected, separator, sid = marker[len("iterm-enhancer Restore "):].partition(":")
        if not separator or len(selected) != 16 or any(c not in "0123456789abcdef" for c in selected):
            return None
        status = await self.capture.call("GET", "/internal/recovery")
        if not any(e["id"] == selected for e in status["entries"]):
            self.recipes[marker] = None  # ordinary history GC is not an API/storage failure
            return None
        saved = await self.capture.call("GET", "/internal/recovery/snapshot/" + selected)
        pane = next((p for w in saved["windows"] for t in w["tabs"] for p in t["panes"] if p["id"] == sid), None)
        if pane:
            self.recipes[marker] = pane
        return pane

    def recipe(self, sid):
        return self.recipes.get(self.known.get(sid))

    def superseded(self, sid):
        marker = self.known.get(sid) or ""
        return any(other != sid and ((self.recipe(other) is not None and self.known.get(other) == marker) or
                   (self.recipe(other) or {}).get("id") == sid) for other in self.live)

    def unconfirmed(self):
        return any(not self.superseded(sid) for sid in set(self.absent) | set(self.exits)) or any(
            not event["revive"] and not self.superseded(sid) for sid, event in self.pending.items())

    def blocked(self, source_id, alias=None):
        events = dict(self.pending)
        for sid in set(self.absent) | set(self.exits):
            events[sid] = {"marker": self.known.get(sid), "revive": False}
        for sid, event in events.items():
            if self.superseded(sid):
                continue
            marker = event.get("marker") or ""
            if not event["revive"] and (sid in (source_id, alias) or
                    marker.startswith("iterm-enhancer Restore ") and marker.partition(":")[2] == source_id):
                return True
        return False

    async def flush(self):
        for sid, event in list(self.pending.items()):
            if not self.operational():
                return
            await self.capture.call("POST", "/internal/recovery/lifecycle", event)
            if self.pending.get(sid) == event:
                self.pending.pop(sid)

    async def poll(self):
        async with self.lock:
            if self.capture.epoch is None:
                self.capture.epoch = await asyncio.to_thread(epoch)
            if self.process is None:
                _, pid, started = self.capture.epoch.rsplit(":", 2)
                self.process = int(pid), int(started)
            if not self.operational():
                self.absent.clear()
                self.exits.clear()
                return
            now = time.monotonic()
            for sid, identity, success in self.watch.drain():
                if self.identities.get(sid) == identity:
                    source = self.recipe(sid)
                    if not success and ((source and source["connection"]["kind"] in ("ssh", "tmux")) or
                            (source is None and (self.known.get(sid) or "").startswith("iterm-enhancer Restore "))):
                        self.failed.add(sid)
                    else:
                        self.exits.setdefault(sid, now)
            sessions = await self.inventory()  # successful full RPC, not a cached App model
            if not self.operational():
                self.absent.clear()
                self.exits.clear()
                return
            if self.last is None or now - self.last > GAP:
                self.absent.clear()  # a gap is not evidence of a deliberate closure
            self.last = now
            self.live = set()
            for sid, session in sessions.items():
                returned = sid in self.absent or self.pending.get(sid, {}).get("revive") is False
                self.absent.pop(sid, None)
                try:
                    marker = await session.async_get_variable("profileName")
                    if not isinstance(marker, str):
                        marker = None
                except Exception:
                    marker = self.known.get(sid)
                self.known[sid] = marker
                source = await self.source(marker)
                identity = await root_identity(session)
                previous = self.identities.get(sid)
                if identity:
                    self.live.add(sid)
                    self.failed.discard(sid)
                if identity and identity == previous and returned:
                    self.queue(sid, True)  # Undo can resume the same paused root during an outage.
                if identity and identity != previous:
                    registered = await self.watch.enroll(session, identity)
                    if registered or await root_identity(session) == identity:
                        # Watching exit status may be denied even when fresh live-root
                        # proof is available; Undo does not require that permission.
                        if previous and previous[0] != identity[0]:
                            self.watch.remove(previous[0])
                        self.identities[sid] = identity
                        self.exits.pop(sid, None)
                        self.queue(sid, True)  # Undo/restart with a freshly proven live root
                if identity is None:
                    try:
                        control = await session.async_get_variable("tmuxRole") == "client"
                    except Exception:
                        control = False
                    if control and (sid not in self.controls or returned):
                        self.controls.add(sid)
                        self.queue(sid, True)  # CC panes have no local root PID; fresh native role is proof.
                    if control:
                        self.live.add(sid)
                    elif source and source["connection"]["kind"] in ("ssh", "tmux") and sid not in self.exits:
                        if await session_state(session) == "ended":
                            self.failed.add(sid)  # bridge restart after an unacknowledged connection failure
            self.failed = {sid for sid in self.failed if not self.superseded(sid)}
            self.connecting = {sid for sid in self.connecting if not self.superseded(sid)}
            self.pending = {sid: event for sid, event in self.pending.items() if event["revive"] or not self.superseded(sid)}
            for sid in set(self.known) - set(sessions):
                self.absent.setdefault(sid, now)
            for sid, since in {**self.absent, **self.exits}.items():
                if now - since >= GRACE:
                    self.queue(sid, False)
            # Recheck after metadata awaits: teardown must not commit a closure.
            if not self.operational():
                return
            await self.flush()
            for sid in list(self.absent):
                if now - self.absent[sid] >= GRACE and sid not in self.pending:
                    self.absent.pop(sid)
                    self.exits.pop(sid, None)
                    self.known.pop(sid, None)
                    self.controls.discard(sid)
                    self.connecting.discard(sid)
                    identity = self.identities.pop(sid, None)
                    if identity:
                        self.watch.remove(identity[0])
            for sid in list(self.exits):
                if now - self.exits[sid] >= GRACE and sid not in self.pending:
                    source = self.recipe(sid)
                    if source and source["connection"]["kind"] in ("ssh", "tmux") and sid in sessions and sid not in self.live:
                        # Only our exact controlled connection marker and observed
                        # ordinary completion authorize closing its retained history.
                        if await sessions[sid].async_get_variable("profileName") == self.known[sid]:
                            await sessions[sid].async_close(force=True)
                    self.exits.pop(sid)
            self.failed.intersection_update(self.known)
            markers = set(self.known.values())
            self.recipes = {m: p for m, p in self.recipes.items() if m in markers}

    async def follow(self):
        try:
            while True:
                try:
                    await asyncio.wait_for(self.poll(), 4)
                except Exception as e:
                    self.absent.clear()
                    message = f"Session closure tracking unavailable ({type(e).__name__}); recovery history preserved"
                    log(message)
                    try:
                        await self.capture.call("POST", "/internal/recovery/error", {"message": message})
                    except Exception:
                        pass
                await asyncio.sleep(0.5)
        finally:
            self.watch.close()
