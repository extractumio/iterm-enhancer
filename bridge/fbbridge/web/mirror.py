# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The terminal mirror, per browser: the shown session's screen, history and input (Client),
over the page's WebSocket. The poll of iTerm's windows they share is in hub.py."""
import asyncio
import json
import math
import time

import iterm2

from ..common import UserError, log
from ..resolve import theme_of
from .httpd import ConnectionClosed
from .merge import MergeError, merge_windows
from .newsession import NewSessionError, new_session, new_window, profiles, reorder_tabs
from .rename import RenameError, rename
from .tmuxkeys import type_into
from .hub import encode, gone
from .trace import Tracer
from .screen import enc_line, line_key
from .fold import History

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
SEND_TIMEOUT = 10      # a browser that takes longer to accept a message is dropped
CLOSED = "This session has closed. Pick another one."
DROPPED = "iTerm2 dropped this tmux connection; its raw output is not shown. Use Reattach above, or pick another session."
NEW_EVERY = 1.0        # seconds between two new sessions from one browser
ACTIONS = {"sub": "open the session", "in": "type into the session", "files": "show the session's files",
           "more": "load older lines", "fit": "resize iTerm", "unfit": "restore iTerm's size",
           "new": "open a new session", "rename": "name the session", "merge": "merge the windows",
           "unfold": "show folded lines"}
UNFOLD_MAX = 20000     # lines one "unfold" may ask for


def screen_key(screen):
    """Identity of a whole screen (cells, styles, cursor, position), to skip an unchanged one."""
    proto = getattr(screen, "_ScreenContents__proto", None)
    return proto.SerializeToString() if proto is not None else None


class Client:
    def __init__(self, conn, hub, ws, max_history, files_of, build=None):
        self.conn, self.hub, self.app, self.ws = conn, hub, hub.app, ws
        self.max_history = max_history
        self.files_of = files_of # session -> {key, cwd, host} for the Files view, or {error}
        self.build = build       # the page's files; a page of another build reloads
        self.stream_task = None
        self.session = None
        self.wake = None         # set to fetch the screen right away
        self.show_in_iterm = True
        self.active_until = 0.0  # loop time until which the screen is polled fast
        self.paused = False      # the page is hidden
        self.last_new = -NEW_EVERY   # loop time of this browser's last new session
        self.typed_at = -math.inf    # loop time of its last key (the window poll waits)
        self.trace = Tracer(self)    # ?trace=1 (AC-55)
        self.reset_screen()

    def reset_screen(self):
        self.top = None          # absolute line number of the screen's first line
        self.overflow = 0        # lines iTerm2 has already dropped from the top
        self.shown = None        # screen_key of the last screen sent
        self.prev = []           # per screen line: (line key, cursor x)
        self.encoded = {}        # (line key, cursor x) -> encoded line, from the last frame
        self.prev_size = None
        self.prev_cursor = None
        self.history = History(self.max_history)   # what this browser has, for folding (AC-56)

    async def send(self, msg):
        """A message (or its encoded text). A stalled browser must not hold up its session's
        stream, or the bridge: it is dropped."""
        text, t = msg if isinstance(msg, str) else encode(msg), time.perf_counter()
        try:
            await asyncio.wait_for(self.ws.send(text), SEND_TIMEOUT)
        except TimeoutError:
            await self.ws.close()
            raise ConnectionClosed()
        self.trace.sent(msg, t, len(text))

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
        t = time.perf_counter()
        history = self.history
        lines = await self.lines(session, first, upto - first) if first < upto else []
        if history is not self.history:
            return                                 # the scrollback was cleared meanwhile: these lines are stale
        self.trace.hist(mode, len(lines), t)
        # folded lines are not sent (None) until the browser asks; lines that scrolled off were (AC-56)
        lines, folds = self.history.take(first, lines, bulk=mode != "append")
        oldest = self.oldest()
        await self.send({"t": "hist", "mode": mode, "sid": session.session_id, "first": first, "oldest": oldest,
                         "truncated": oldest > self.overflow, "lines": lines, **({"folds": folds} if folds else {})})

    async def send_folds(self, session, now):
        """Folds among the lines that scrolled off the screen, at most every second."""
        if self.history.due(now) and (found := self.history.scan(now)):
            await self.send({"t": "folds", "sid": session.session_id, "items": found})

    async def unfold(self, a, b):
        """A fold's lines, when the browser shows it: only lines of a fold it was told of, still in
        iTerm (its top may have gone); else why not, which the page shows."""
        session, history = self.session, self.history
        if not session or self.top is None:
            return
        self.overflow = (await session.async_get_line_info()).overflow
        a = max(a, self.oldest())
        why = ("These lines are no longer in iTerm's history." if a > b or b >= self.top else
               "These lines are not a fold of this session." if not history.told(a, b) else
               f"More than {UNFOLD_MAX} lines: open them in iTerm." if b - a >= UNFOLD_MAX else None)
        if why:
            return await self.send({"t": "lines", "sid": session.session_id, "first": a, "lines": [], "error": why})
        lines = await self.lines(session, a, b - a + 1)
        if self.history is history and self.session is session:   # not cleared or switched meanwhile
            await self.send({"t": "lines", "sid": session.session_id, "first": a, "lines": lines})

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
            if self.top is not None:
                self.history = History(self.max_history)        # its line numbers mean other lines now
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
        if full or changes:     # with the keys it shows, when tracing: the cursor moved or its row changed
            moved = (cx, cy) != self.prev_cursor or any(i == cy for i, _ in changes)
            await self.send({"t": "screen", "sid": session.session_id, "full": full, "n": screen.number_of_lines,
                             "cols": size[0], "rows": size[1], "ch": changes, **self.trace.frame(moved)})
        self.prev_size, self.prev_cursor = size, (cx, cy)
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
            self.trace.woken("notify")
            changed.set()

        sub = await iterm2.notifications.async_subscribe_to_screen_update_notification(
            self.conn, on_update, session.session_id)
        try:
            ticks = 0
            while True:
                changed.clear()
                if self.hub.suspect(session.session_id):      # raw tmux protocol: not mirrored (AC-54)
                    return await self.send({"t": "error", "msg": DROPPED, "sid": session.session_id})
                t = time.perf_counter()
                screen = await session.async_get_screen_contents()
                self.trace.read(t)
                if await self.send_screen(session, screen):
                    self.active_until = loop.time() + ACTIVE_FOR
                    await asyncio.sleep(FRAME_GAP)
                await self.send_folds(session, loop.time())
                busy = loop.time() < self.active_until
                try:
                    await asyncio.wait_for(changed.wait(), POLL_ACTIVE if busy else POLL_IDLE)
                except TimeoutError:
                    self.trace.woken("poll")
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
        t = time.perf_counter()
        await self.stop_stream()
        await self.restore_sizes()       # "Fit" applies to the shown session only
        self.session = self.app.get_session_by_id(sid)
        self.reset_screen()
        if self.session:
            self.trace.opening(self.session, t)
        else:
            await self.send({"t": "error", "msg": CLOSED, "sid": sid})   # the page drops it once it shows another pane
            return
        if self.hub.suspect(sid):
            return await self.send({"t": "error", "msg": DROPPED, "sid": sid})
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
            if gone(e):
                await self.send_quietly({"t": "error", "msg": CLOSED, "sid": self.session.session_id})
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
        elif t == "in" and self.session and self.hub.suspect(self.session.session_id):
            await self.send({"t": "error", "msg": DROPPED, "sid": self.session.session_id})   # it would reach tmux as commands
        elif t == "in" and self.session:
            key = self.trace.key(msg)              # timed step by step when tracing (AC-55)
            if self.show_in_iterm:
                await self.bring_tab_forward()
            self.trace.step(key, "fwd")
            self.trace.step(key, "type", via=await type_into(self.conn, self.session, msg["data"]))
            self.trace.typed(key)
            self.typed_at = asyncio.get_running_loop().time()
            self.active_until = self.typed_at + ACTIVE_FOR
            if self.wake:
                self.trace.woken("wake")
                self.wake.set()                    # show the echo now, not at the next poll
        elif t == "trace":
            await self.trace.handle(msg)
        elif t == "files" and self.session:
            await self.send({"t": "files", "sid": self.session.session_id, **await self.files_of(self.session)})
        elif t == "more":
            await self.send_older(int(msg["before"]))
        elif t == "unfold":
            await self.unfold(int(msg["n"]), int(msg["to"]))
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
        elif t == "new":
            await self.create(str(msg.get("group", "")))
        elif t == "newwindow":
            await self.create(None, str(msg.get("profile", "")))
        elif t == "reorder":
            try:
                await reorder_tabs(self.app, str(msg.get("group", "")), [str(i) for i in msg.get("ids", [])])
            except NewSessionError as e:
                await self.send({"t": "error", "msg": str(e)})
            await self.hub.refresh()
        elif t == "profiles":
            await self.send({"t": "profiles", "items": await profiles(self.conn)})
        elif t in ("reattach", "dismiss"):
            await self.drop_action(t, str(msg.get("id", "")))
        elif t == "rename":
            await self.rename(str(msg.get("id", "")), msg.get("name", ""))
        elif t == "merge":
            await self.merge()
        elif t == "resume":                        # back: only what changed meanwhile is sent
            self.paused = False
            if self.session and not self.stream_task:
                self.stream_task = asyncio.create_task(self.guard(self.stream(self.session)))

    async def create(self, window_id, profile=None):
        """[+]: a new session in that group after the shown pane, or "New window" with
        `profile`; the list has it before the page is told to show it."""
        now = asyncio.get_running_loop().time()
        if now - self.last_new < NEW_EVERY:
            return await self.send({"t": "error", "msg": "One new session a second: press + again."})
        self.last_new = now
        try:
            if window_id is None:
                sid, note = await new_window(self.conn, self.app, profile), None
            else:
                sid, note = await new_session(self.conn, self.app, window_id, self.session.session_id if self.session else None)
        except NewSessionError as e:
            return await self.send({"t": "error", "msg": str(e)})
        await self.hub.refresh()
        await self.send({"t": "layout", "groups": self.hub.layout})    # in order: the list has it, then show it
        await self.send({"t": "created", "id": sid, "note": note})

    async def leave_dropped(self):
        """The shown gateway turned out dropped: stop mirroring it."""
        sid = self.session.session_id
        await self.stop_stream()
        await self.send_quietly({"t": "error", "msg": DROPPED, "sid": sid})

    async def drop_action(self, t, drop_id):
        tmux = self.hub.tmux
        if not tmux or drop_id not in tmux.dropped:
            return await self.send({"t": "error", "msg": "This tmux session was reattached or dismissed already."})
        if t == "dismiss":
            return tmux.dismiss(drop_id)
        try:
            await tmux.reattach(drop_id)
        except UserError as e:
            await self.send({"t": "error", "msg": str(e)})

    async def rename(self, sid, name):
        """The pencil: name the pane; the list shows it at once."""
        session = self.app.get_session_by_id(sid)
        if not session:
            return await self.send({"t": "error", "msg": CLOSED, "sid": sid})
        try:
            await rename(self.conn, session, name)
        except RenameError as e:
            return await self.send({"t": "error", "msg": str(e), "sid": sid})
        await self.hub.refresh()

    async def merge(self):
        """"Merge windows": iTerm2's tabs into one window, and one per tmux session; the list shows it at once."""
        if self.hub.merging.locked():
            return await self.send({"t": "error", "msg": "The windows are being merged already."})
        async with self.hub.merging:
            await self.app.async_refresh()
            try:
                text = await merge_windows(self.app, self.app.terminal_windows, self.session.session_id if self.session else None)
            except MergeError as e:
                return await self.send({"t": "error", "msg": str(e)})
            await self.hub.refresh()
        await self.send({"t": "note", "msg": text})

    async def run(self):
        """The page signed in (its cookie was checked before the WebSocket was accepted)."""
        try:
            if self.build:
                await self.send({"t": "build", "id": self.build})     # first: an outdated page reloads
            await self.hub.join(self)
            async for raw in self.ws:
                msg = {}
                try:
                    msg, t = json.loads(raw), time.perf_counter()
                    await self.handle(msg)
                    self.trace.handled(msg.get("t"), t)
                except ConnectionClosed:
                    raise
                except Exception as e:  # fail loud: in words on the page, and in the log
                    t = msg.get("t") if isinstance(msg, dict) else None
                    action = ACTIONS.get(t, "do that")
                    log(f"web: {t}: {e!r}")
                    text = f"iTerm2 no longer has a session needed to {action}." if gone(e) else f"Could not {action}: {e}"
                    await self.send_quietly({"t": "error", "msg": text})
        except ConnectionClosed:
            pass        # a phone that sleeps or switches network drops the socket without a goodbye
        finally:
            self.hub.clients.discard(self)
            self.trace.close()
            await self.stop_stream()
            await self.hub.release(self)     # a phone that goes away must not leave iTerm shrunk
