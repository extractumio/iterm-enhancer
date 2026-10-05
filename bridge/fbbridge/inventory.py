# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Authoritative live identities, independent of focused-pane health (AC-44)."""
import asyncio
import time

import iterm2

from .common import log
from . import procinfo, resolve

GRACE = 60
GAP = 15


class Inventory:
    def __init__(self):
        self.absent = {}
        self.last = None

    def prune(self, sessions, now):
        if self.last is None or now - self.last > GAP:
            self.absent.clear()
        self.last = now
        for sid in list(resolve._static):
            if sid in sessions:
                self.absent.pop(sid, None)
            elif now - self.absent.setdefault(sid, now) >= GRACE:
                resolve._static.pop(sid, None)
                self.absent.pop(sid, None)
        procinfo._tmux_args = {pid: args for pid, args in procinfo._tmux_args.items()
                              if procinfo.proc_name(pid) == "tmux"}

    async def snapshot(self, conn):
        response = await iterm2.rpc.async_list_sessions(conn)
        model = response.list_sessions_response
        windows = [iterm2.Window.create_from_proto(conn, w) for w in model.windows]
        if any(w is None for w in windows):
            raise ValueError("Incomplete window inventory")
        sessions = [s for w in windows for t in w.tabs for s in t.all_sessions]
        sessions += [iterm2.Session(conn, None, s) for s in model.buried_sessions]
        ids = {s.session_id for s in sessions}
        keys = set(ids)
        protect_tmux = False
        limit = asyncio.Semaphore(4)

        async def key_of(s):
            async with limit:
                try:
                    r = await resolve.resolve(conn, s)
                    return r["key"], r["mode"]
                except Exception:
                    return None, "unknown"

        for key, mode in await asyncio.gather(*(key_of(s) for s in sessions)):
            if key:
                keys.add(key)
            # A plain tmux client hides its other panes. A failed resolution is not
            # evidence that any recoverable tmux workspace is gone.
            protect_tmux |= mode in ("tmux", "unknown") or (mode == "tmux -CC" and not key.startswith("tmux:"))
        self.prune(ids, time.monotonic())
        return {"windows": [w.window_id for w in windows], "sessions": sorted(ids),
                "keys": sorted(keys), "protect_tmux": protect_tmux}

    async def follow(self, conn, post):
        while True:
            try:
                value = await asyncio.wait_for(self.snapshot(conn), GAP - 1)
                await asyncio.get_running_loop().run_in_executor(None, post, "/internal/liveness", value)
            except Exception as e:
                log(f"inventory unavailable ({type(e).__name__}); cleanup paused")
            await asyncio.sleep(5)
