# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""One poll of iTerm's windows, tabs and titles, shared by every signed-in browser (mirror.py
serves each one), and the sizes browsers changed."""
import asyncio
import json
import time

import iterm2

from ..common import log
from ..rpcstats import tag
from .altspans import Spans
from .bookmarks import Store
from .layout import layout_of
from .trace import ms, note

LAYOUT_EVERY = 2       # seconds between window/tab/title polls
TYPING_QUIET = 1.0     # the poll waits while a key came this recently (it slows the echo)
TYPING_WAIT = 10.0     # but not longer than this
ALT = "showingAlternateScreen"   # a full-screen program (mc, vim, a coding agent's full-screen mode) runs


def is_on(value):
    """iTerm2 gives a flag variable as 1, "1" or '"1"' depending on the call."""
    return str(value).strip('"') == "1"


def gone(e):
    """iTerm2's answer for a session that has closed (its RPCException names it)."""
    return "SESSION_NOT_FOUND" in str(e)


def encode(msg):
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False)


class Hub:
    """One poll of iTerm's windows, tabs and titles, shared by every signed-in browser; and
    the original size of each session a browser resized (Fit iTerm), shared too."""

    def __init__(self, app, tmux=None, marks=None):
        self.app, self.tmux = app, tmux      # tmux: the bridge's watch for dropped integrations (AC-54)
        self.clients = set()
        if tmux:
            tmux.listeners.append(self.drops_changed)
        self.layout = None
        self.fitted = {}         # session id -> [original (cols, rows), browsers that fitted it]
        self.merging = asyncio.Lock()   # two browsers merging at once would move tabs of closed windows
        self.marks = Store(marks)        # bookmarks (AC-57); marks: their file, none for a test's Hub
        self.stalled = None              # when iTerm2 stopped answering (stall.py), None while it answers
        self.alt = Spans()               # lines full-screen programs scrolled off, per session (AC-56)
        self.alt_sub = None

    async def run(self):
        tag("web list")
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
            await self.follow_alt()
            await asyncio.sleep(LAYOUT_EVERY)

    def stop(self):
        if self.tmux and self.drops_changed in self.tmux.listeners:
            self.tmux.listeners.remove(self.drops_changed)

    async def watch(self):
        """Follow every session's alternate screen (iTerm2 sends one notice per switch) and note
        the sessions on it now, so a pane a browser opens later has its lines folded too; what a
        program running at the bridge's start scrolled off before stays in sight."""
        async def on_alt(_conn, msg):
            await self.saw_alt(msg.identifier, is_on(msg.json_new_value))
        self.alt_sub = await iterm2.notifications.async_subscribe_to_variable_change_notification(
            self.app.connection, on_alt, iterm2.VariableScopes.SESSION.value, ALT, "all")
        for w in self.app.terminal_windows:
            for t in w.tabs:
                for s in t.sessions:
                    if is_on(await s.async_get_variable(ALT)):
                        await self.saw_alt(s.session_id, True)

    async def unwatch(self):
        if self.alt_sub:
            await iterm2.notifications.async_unsubscribe(self.app.connection, self.alt_sub)
            self.alt_sub = None

    async def saw_alt(self, sid, on):
        """A session's flag switched: a span opens where its screen's first line is now, or
        closes where the line was last seen while the flag was on (never at the shell's lines
        printed after the program)."""
        if not on:
            return self.alt.close(sid)
        where = await self.line_of(sid)
        if where:
            self.alt.open(sid, *where)

    async def follow_alt(self):
        """Where the first line is now in every session on its alternate screen: a program nobody
        watches scrolls too, and its span must end where it was, not where the shell got after."""
        for sid in [sid for sid in self.alt.spans if self.alt.running(sid)]:
            if (where := await self.line_of(sid)) and self.alt.running(sid):     # not switched off meanwhile
                self.alt.grew(sid, *where)

    async def line_of(self, sid):
        """(the screen's first line, the pane's width) of a session, None when it is gone."""
        session = self.app.get_session_by_id(sid)
        if not session:
            return None
        try:
            info = await session.async_get_line_info()
        except Exception as e:        # surface, keep following
            if not gone(e):
                log(f"web: alt: {e!r}")
            return None
        return info.scrollback_buffer_height + info.overflow, session.grid_size.width

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

    def tell(self, msg):
        """msg to every browser; a stalled one holds up nobody."""
        text = encode(msg)
        for c in list(self.clients):
            asyncio.create_task(c.send_quietly(text))

    async def refresh(self):
        """Ask iTerm2 for its windows now and tell every browser what changed."""
        try:
            t = time.perf_counter()
            lay = await layout_of(self.app)
            note("poll", ms=ms(t))
        except Exception as e:       # surface, keep polling
            log(f"web: layout: {e!r}")
            return
        self.alt.keep({i.get("id") for g in lay for i in g.get("items", ())})   # closed sessions' spans go
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
        self.alt.clear(sid)                   # the history wraps anew: its numbers mean other lines

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
                        self.alt.clear(sid)
                except Exception as e:
                    if not gone(e):          # a closed session has no size to give back
                        raise

    async def join(self, client):
        self.clients.add(client)
        if self.layout is None:
            self.layout = await layout_of(self.app)
        await client.send({"t": "layout", "groups": self.layout})
        if self.stalled is not None:
            await client.send({"t": "iterm", "ok": False, "ms": round((asyncio.get_running_loop().time() - self.stalled) * 1000)})
        await client.send(self.drops())
        await client.send({"t": "marks", "items": self.marks.all(), "error": self.marks.broken})
