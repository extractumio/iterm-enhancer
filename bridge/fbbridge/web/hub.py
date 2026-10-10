# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""One poll of iTerm's windows, tabs and titles, shared by every signed-in browser (mirror.py
serves each one), and the sizes browsers changed."""
import asyncio
import json
import time

import iterm2

from ..common import log
from .layout import layout_of
from .trace import ms, note

LAYOUT_EVERY = 2       # seconds between window/tab/title polls
TYPING_QUIET = 1.0     # the poll waits while a key came this recently (it slows the echo)
TYPING_WAIT = 10.0     # but not longer than this


def gone(e):
    """iTerm2's answer for a session that has closed (its RPCException names it)."""
    return "SESSION_NOT_FOUND" in str(e)


def encode(msg):
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False)


class Hub:
    """One poll of iTerm's windows, tabs and titles, shared by every signed-in browser; and
    the original size of each session a browser resized (Fit iTerm), shared too."""

    def __init__(self, app, tmux=None):
        self.app, self.tmux = app, tmux      # tmux: the bridge's watch for dropped integrations (AC-54)
        self.clients = set()
        if tmux:
            tmux.listeners.append(self.drops_changed)
        self.layout = None
        self.fitted = {}         # session id -> [original (cols, rows), browsers that fitted it]
        self.merging = asyncio.Lock()   # two browsers merging at once would move tabs of closed windows

    async def run(self):
        loop = asyncio.get_running_loop()
        polled = loop.time()
        while True:
            now = loop.time()
            typing = any(now - c.typed_at < TYPING_QUIET for c in self.clients) and now - polled < TYPING_WAIT
            if typing:
                await asyncio.sleep(TYPING_QUIET / 2)
                continue
            if any(not c.paused for c in self.clients):     # nobody looking: nothing to ask iTerm2
                await self.refresh()
                polled = loop.time()
            await asyncio.sleep(LAYOUT_EVERY)

    def stop(self):
        if self.tmux and self.drops_changed in self.tmux.listeners:
            self.tmux.listeners.remove(self.drops_changed)

    def drops(self):
        return {"t": "drops", "items": self.tmux.items() if self.tmux else []}

    def suspect(self, sid):
        return bool(self.tmux and self.tmux.suspect(sid))

    def drops_changed(self):
        text = encode(self.drops())
        for c in list(self.clients):
            asyncio.create_task(c.send_quietly(text))
            if c.session and self.suspect(c.session.session_id):
                asyncio.create_task(c.leave_dropped())

    async def refresh(self):
        """Ask iTerm2 for its windows now and tell every browser what changed."""
        try:
            t = time.perf_counter()
            lay = await layout_of(self.app)
            note("poll", ms=ms(t))
        except Exception as e:       # surface, keep polling
            log(f"web: layout: {e!r}")
            return
        if lay != self.layout:
            self.layout = lay
            text = encode({"t": "layout", "groups": lay})       # once, for every browser
            for c in list(self.clients):
                asyncio.create_task(c.send_quietly(text))     # a stalled browser holds up nobody

    async def fit(self, client, session, cols, rows):
        sid = session.session_id
        if sid not in self.fitted:
            g = session.grid_size  # a live protobuf that iTerm2 updates in place: copy the numbers
            self.fitted[sid] = [(g.width, g.height), set()]
        self.fitted[sid][1].add(client)
        await session.async_set_grid_size(iterm2.util.Size(max(20, cols), max(5, rows)))

    async def release(self, client):
        """client no longer wants its fits; the last browser to let go restores iTerm's size."""
        for sid, (size, owners) in list(self.fitted.items()):
            if client not in owners:
                continue
            owners.discard(client)
            if not owners:
                del self.fitted[sid]
                session = self.app.get_session_by_id(sid)
                try:
                    if session:
                        await session.async_set_grid_size(iterm2.util.Size(*size))
                except Exception as e:
                    if not gone(e):          # a closed session has no size to give back
                        raise

    async def join(self, client):
        self.clients.add(client)
        if self.layout is None:
            self.layout = await layout_of(self.app)
        await client.send({"t": "layout", "groups": self.layout})
        await client.send(self.drops())
