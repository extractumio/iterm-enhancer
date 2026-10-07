# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The terminal mirror: one shared poll of iTerm's windows and titles (Hub), and per browser
the shown session's screen, history and input (Client), over the page's WebSocket."""
import asyncio
import json

import iterm2

from ..common import log
from ..resolve import theme_of
from .httpd import ConnectionClosed
from .layout import layout_of
from .screen import enc_line, line_key

FIRST_HISTORY = 1000   # scrollback lines sent when a session opens; older ones load on scroll
PAGE = 1000            # lines per "load older" request
CHUNK = 500            # lines per async_get_contents call
FRAME_GAP = 0.03       # shortest gap between two screen frames (max ~30 fps)
# iTerm2 notifies screen changes only when it redraws, and it hardly redraws a tab that is not
# in front (measured: 2 notifications/s while Claude Code animated). So the screen is also
# polled: fast right after typing or a change, slowly when idle. An unchanged screen costs one
# request and one comparison.
POLL_ACTIVE = 0.05     # seconds between polls while busy
POLL_IDLE = 0.25       # seconds between polls when nothing changed for ACTIVE_FOR
ACTIVE_FOR = 3.0
LAYOUT_EVERY = 2       # seconds between window/tab/title polls
SEND_TIMEOUT = 10      # a browser that takes longer to accept a message is dropped
CLOSED = "This session has closed. Pick another one."


def encode(msg):
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False)


def screen_key(screen):
    """Identity of a whole screen (cells, styles, cursor, position), to skip an unchanged one."""
    proto = getattr(screen, "_ScreenContents__proto", None)
    return proto.SerializeToString() if proto is not None else None


class Hub:
    """One poll of iTerm's windows, tabs and titles, shared by every signed-in browser; and
    the original size of each session a browser resized (Fit iTerm), shared too."""

    def __init__(self, app):
        self.app = app
        self.clients = set()
        self.layout = None
        self.fitted = {}         # session id -> [original (cols, rows), browsers that fitted it]

    async def run(self):
        while True:
            if any(not c.paused for c in self.clients):     # nobody looking: nothing to ask iTerm2
                try:
                    lay = await layout_of(self.app)
                except Exception as e:       # surface, keep polling
                    log(f"web: layout: {e!r}")
                    lay = self.layout
                if lay != self.layout:
                    self.layout = lay
                    text = encode({"t": "layout", "groups": lay})       # once, for every browser
                    for c in list(self.clients):
                        asyncio.create_task(c.send_quietly(text))
            await asyncio.sleep(LAYOUT_EVERY)

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
                if session:
                    await session.async_set_grid_size(iterm2.util.Size(*size))

    async def join(self, client):
        self.clients.add(client)
        if self.layout is None:
            self.layout = await layout_of(self.app)
        await client.send({"t": "layout", "groups": self.layout})


class Client:
    def __init__(self, conn, hub, ws, max_history, files_of):
        self.conn, self.hub, self.app, self.ws = conn, hub, hub.app, ws
        self.max_history = max_history
        self.files_of = files_of # session -> {key, cwd, host} for the Files view, or {error}
        self.stream_task = None
        self.session = None
        self.wake = None         # set to fetch the screen right away
        self.show_in_iterm = True
        self.active_until = 0.0  # loop time until which the screen is polled fast
        self.paused = False      # the page is hidden
        self.reset_screen()

    def reset_screen(self):
        self.top = None          # absolute line number of the screen's first line
        self.overflow = 0        # lines iTerm2 has already dropped from the top
        self.shown = None        # screen_key of the last screen sent
        self.prev = []           # per screen line: (line key, cursor x)
        self.encoded = {}        # (line key, cursor x) -> encoded line, from the last frame
        self.prev_size = None

    async def send(self, msg):
        """A message (or its encoded text). A stalled browser must not hold up its session's
        stream, or the bridge: it is dropped."""
        try:
            await asyncio.wait_for(self.ws.send(msg if isinstance(msg, str) else encode(msg)), SEND_TIMEOUT)
        except TimeoutError:
            await self.ws.close()
            raise ConnectionClosed()

    async def send_quietly(self, msg):
        try:
            await self.send(msg)
        except Exception:
            pass

    # ---------- history ----------

    async def lines(self, session, first, count):
        """Lines first..first+count, asked for in chunks at once."""
        end = first + count
        chunks = await asyncio.gather(*(session.async_get_contents(s, min(CHUNK, end - s)) for s in range(first, end, CHUNK)))
        return [enc_line(l) for chunk in chunks for l in chunk]

    def oldest(self):
        """The oldest line a browser may load: iTerm's own limit or ours, whichever is newer."""
        return max(self.overflow, self.top - self.max_history)

    async def send_hist(self, session, mode, first, upto):
        """Lines first..upto as a reset, an append or a prepend of the browser's history."""
        lines = await self.lines(session, first, upto - first) if first < upto else []
        oldest = self.oldest()
        await self.send({"t": "hist", "mode": mode, "sid": session.session_id, "first": first, "oldest": oldest,
                         "truncated": oldest > self.overflow, "lines": lines})

    async def send_older(self, before):
        session = self.session
        if not session or self.top is None:
            return
        self.overflow = (await session.async_get_line_info()).overflow
        before = max(self.oldest(), min(before, self.top))      # never trust the browser's range
        await self.send_hist(session, "prepend", max(self.oldest(), before - PAGE), before)

    # ---------- screen ----------

    async def send_screen(self, session, screen):
        """Send what changed since the last frame; False when nothing did."""
        key = screen_key(screen)
        if key is not None and key == self.shown:
            return False
        self.shown = key
        top = screen.windowed_coord_range.coordRange.start.y  # absolute; the cursor uses it too
        if self.top is None or top < self.top:                 # first frame or scrollback cleared
            self.top = top
            self.overflow = (await session.async_get_line_info()).overflow
            await self.send_hist(session, "reset", max(self.oldest(), top - FIRST_HISTORY), top)
        elif top > self.top:                                    # lines scrolled off the top
            prev_top, self.top = self.top, top
            await self.send_hist(session, "append", max(self.oldest(), prev_top), top)
        size = (session.grid_size.width, session.grid_size.height, screen.number_of_lines)
        full = size != self.prev_size
        cx, cy = screen.cursor_coord.x, screen.cursor_coord.y - top
        prev, encoded, self.prev, self.encoded, changes = self.prev, self.encoded, [], {}, []
        for i in range(screen.number_of_lines):
            line = screen.line(i)
            k = (line_key(line), cx if i == cy else None)
            self.prev.append(k)
            if not full and i < len(prev) and k[0] is not None and prev[i] == k:
                self.encoded[k] = encoded[k]
                continue
            # a line that only moved (output scrolled) is reused, not encoded again
            enc = encoded.get(k) if k[0] is not None else None
            self.encoded[k] = enc = enc or enc_line(line, k[1])
            changes.append([i, enc])
        if full or changes:
            await self.send({"t": "screen", "sid": session.session_id, "full": full, "n": screen.number_of_lines,
                             "cols": size[0], "rows": size[1], "ch": changes})
        self.prev_size = size
        return bool(full or changes)

    async def stream(self, session):
        """Push theme, history and then every screen change until stopped."""
        theme = await theme_of(self.app, session)
        await self.send({"t": "theme", "sid": session.session_id, "theme": theme})
        # Not session.get_screen_streamer(): it drops updates that arrive while no get() is
        # pending, so the tail of a burst of output would never be shown.
        changed = self.wake = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def on_update(_conn, _msg):
            changed.set()

        sub = await iterm2.notifications.async_subscribe_to_screen_update_notification(
            self.conn, on_update, session.session_id)
        try:
            ticks = 0
            while True:
                changed.clear()
                if await self.send_screen(session, await session.async_get_screen_contents()):
                    self.active_until = loop.time() + ACTIVE_FOR
                    await asyncio.sleep(FRAME_GAP)
                busy = loop.time() < self.active_until
                try:
                    await asyncio.wait_for(changed.wait(), POLL_ACTIVE if busy else POLL_IDLE)
                except TimeoutError:
                    pass
                ticks += 1
                if ticks % 40 == 0 and not busy:        # follow light/dark and profile switches
                    new = await theme_of(self.app, session)
                    if new != theme:
                        theme = new
                        await self.send({"t": "theme", "sid": session.session_id, "theme": theme})
        finally:
            await iterm2.notifications.async_unsubscribe(self.conn, sub)

    async def stop_stream(self):
        """Cancel the stream and wait, so it cannot send a stale frame after a switch."""
        if self.stream_task:
            self.stream_task.cancel()
            try:
                await self.stream_task
            except asyncio.CancelledError:
                pass
            self.stream_task = None

    async def subscribe(self, sid):
        await self.stop_stream()
        await self.restore_sizes()       # "Fit" applies to the shown session only
        self.session = self.app.get_session_by_id(sid)
        self.reset_screen()
        if not self.session:
            await self.send({"t": "error", "msg": CLOSED})
            return
        if self.show_in_iterm:
            await self.bring_tab_forward()       # a shown tab also refreshes at full speed
        self.stream_task = asyncio.create_task(self.guard(self.stream(self.session)))

    async def guard(self, coro):
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except ConnectionClosed:
            return          # the browser went away; run() cleans up
        except Exception as e:  # surface, never swallow
            if "SESSION_NOT_FOUND" in str(e):
                await self.send_quietly({"t": "error", "msg": CLOSED})
                return
            log(f"web: stream: {e!r}")
            await self.send_quietly({"t": "error", "msg": f"Lost the session stream: {e}"})

    # ---------- size ----------

    async def bring_tab_forward(self):
        """iTerm2 processes a hidden tab's output only a few times a second (measured: echo after
        170-830 ms, against 7-20 ms for a shown tab). Typing from the browser therefore selects
        the session's tab in its iTerm window; the window is not raised and keeps its place."""
        s = self.session
        w = s.window
        if w and w.current_tab and s.tab and w.current_tab.tab_id != s.tab.tab_id:
            await s.async_activate(select_tab=True, order_window_front=False)

    async def restore_sizes(self):
        await self.hub.release(self)
        await self.send_quietly({"t": "fit", "on": False})

    # ---------- protocol ----------

    async def handle(self, msg):
        t = msg.get("t")
        if t == "sub":
            await self.subscribe(msg["id"])
        elif t == "in" and self.session:
            if self.show_in_iterm:
                await self.bring_tab_forward()
            await self.session.async_send_text(msg["data"], suppress_broadcast=True)
            self.active_until = asyncio.get_running_loop().time() + ACTIVE_FOR
            if self.wake:
                self.wake.set()                    # show the echo now, not at the next poll
        elif t == "files" and self.session:
            await self.send({"t": "files", "sid": self.session.session_id, **await self.files_of(self.session)})
        elif t == "more":
            await self.send_older(int(msg["before"]))
        elif t == "fit" and self.session:
            await self.hub.fit(self, self.session, int(msg["cols"]), int(msg["rows"]))
            await self.send({"t": "fit", "on": True})
        elif t == "unfit":
            await self.restore_sizes()
        elif t == "prefs":
            self.show_in_iterm = bool(msg.get("showInIterm", True))
        elif t == "pause":                         # the page is hidden: stop polling for it
            self.paused = True
            await self.stop_stream()
        elif t == "resume":                        # back: only what changed meanwhile is sent
            self.paused = False
            if self.session and not self.stream_task:
                self.stream_task = asyncio.create_task(self.guard(self.stream(self.session)))

    async def run(self):
        """The page signed in (its cookie was checked before the WebSocket was accepted)."""
        try:
            await self.hub.join(self)
            async for raw in self.ws:
                try:
                    await self.handle(json.loads(raw))
                except ConnectionClosed:
                    raise
                except Exception as e:
                    await self.send_quietly({"t": "error", "msg": str(e)})
        except ConnectionClosed:
            pass        # a phone that sleeps or switches network drops the socket without a goodbye
        finally:
            self.hub.clients.discard(self)
            await self.stop_stream()
            await self.hub.release(self)     # a phone that goes away must not leave iTerm shrunk
