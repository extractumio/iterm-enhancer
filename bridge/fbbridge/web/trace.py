# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Latency trace (AC-55): with ?trace=1 the page and the bridge record each key's way to its
echo, sessions opened, round trips and stalls, one JSON record per line in the log folder, for
TRACE_FOR seconds on the bridge's clock. Nothing typed or shown is recorded: kinds, counts,
times, session ids and the session's mode. The page's records are kept only as known names
with numbers, booleans and listed words."""
import asyncio
import itertools
import json
import math
import os
import re
import time

import iterm2

from .. import common
from ..common import log
from .merge import is_tmux

TRACE_FOR = 15 * 60
MAX_BYTES = 20 * 1024 * 1024
MAX_BATCH = 500            # page records per message
EVERY = 2.0                # seconds between flushes and checks
PROBE_EVERY = 6.0          # seconds between tmux round trips (they share the channel with send-keys)
PROBE_TIMEOUT = 2.0
KEEP_FILES = 10            # older trace files go when a new one starts
LAG_EVERY = 0.1            # the event loop is looked at this often
LAG_NOTE = 0.02            # and noted when it runs this late
SLOW_MSG = 0.02            # a handled message slower than this is noted
STALE = 10.0               # a key without an echo by then is noted without one

WORDS = {
    "path": {"keydown", "input", "compose", "hotkey", "paste", "tap", "other"},
    "cls": {"char", "text", "enter", "backspace", "tab", "esc", "seq", "ctrl", "none"},
    "via": {"text", "keys"},
    "woke": {"first", "next", "wake", "notify", "poll"},
    "what": {"screen", "hist"},
}
SID = re.compile(r"^[\w:.%-]{1,80}$")
PAGE = {     # what a page may record: event -> its fields (numbers or booleans unless in WORDS or "sid")
    "echo": {"k", "path", "cls", "n", "comp", "in", "buf", "rt", "parse", "draw", "paint", "total",
             "fwd", "type", "via", "wait", "polls", "woke", "read", "enc", "skip", "bt"},
    "noecho": {"k", "path", "cls", "n"},
    "open": {"sid", "first", "paint", "bytes"},
    "ping": {"n", "rtt"},
    "jank": {"count", "max"},
    "draw": {"what", "ms", "rows"},
}
CLIENTS = itertools.count(1)


def ms(since):
    return round((time.perf_counter() - since) * 1000, 2)


def clean(rec):
    """A page record with only what it may hold, or None."""
    if not isinstance(rec, dict) or rec.get("ev") not in PAGE:
        return None
    out = {"ev": rec["ev"]}
    for name in PAGE[rec["ev"]]:
        v = rec.get(name)
        if name in WORDS:
            ok = isinstance(v, str) and v in WORDS[name]
        elif name == "sid":
            ok = isinstance(v, str) and bool(SID.match(v))
        else:
            ok = isinstance(v, bool) or (isinstance(v, (int, float)) and math.isfinite(v) and abs(v) < 1e9)
        if ok:
            out[name] = round(v, 2) if isinstance(v, float) else v
    return out


class Recorder:
    """The trace file, shared by every browser: one at a time, ended on the bridge's clock."""

    def __init__(self):
        self.file = self.path = self.task = None
        self.until = 0.0
        self.size = 0

    @property
    def on(self):
        return self.file is not None and time.monotonic() < self.until and self.size < MAX_BYTES

    def left(self):
        return max(0, round(self.until - time.monotonic())) if self.on else 0

    def start(self):
        self.stop()
        common.LOG_DIR.mkdir(parents=True, exist_ok=True)
        for old in sorted(common.LOG_DIR.glob("trace-*.jsonl"))[:-(KEEP_FILES - 1)]:
            old.unlink(missing_ok=True)
        self.path = common.LOG_DIR / time.strftime("trace-%Y%m%d-%H%M%S.jsonl")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.file = os.fdopen(fd, "a", buffering=1 << 16)
        self.until, self.size = time.monotonic() + TRACE_FOR, 0
        self.task = asyncio.create_task(self.watch())
        log(f"web: trace on: {self.path}")

    def stop(self):
        if self.task:
            self.task.cancel()
            self.task = None
        if self.file:
            self.file.close()
            self.file = None
            log(f"web: trace off: {self.path} ({self.size} bytes)")

    def write(self, src, rec):
        if not self.on:
            return
        line = json.dumps({"at": round(time.time() * 1000), "src": src, **rec}, separators=(",", ":")) + "\n"
        self.size += len(line)
        try:
            self.file.write(line)
        except OSError as e:          # a full disk ends the trace, never typing or the stream
            log(f"web: trace: {e!r}")
            self.stop()

    async def watch(self):
        """The event loop's lateness, and the file flushed; the end on time or size."""
        loop = asyncio.get_running_loop()
        flushed = loop.time()
        while self.on:
            t = loop.time()
            await asyncio.sleep(LAG_EVERY)
            late = loop.time() - t - LAG_EVERY
            if late > LAG_NOTE:
                self.write("bridge", {"ev": "lag", "ms": round(late * 1000, 1)})
            if loop.time() - flushed > EVERY:
                try:
                    self.file.flush()
                except OSError as e:
                    log(f"web: trace: {e!r}")
                    break
                flushed = loop.time()
        self.task = None
        self.stop()


RECORDER = Recorder()


def note(ev, **fields):
    """A bridge record, when a trace is on."""
    if RECORDER.on:
        RECORDER.write("bridge", {"ev": ev, **fields})


def mode_of(session, layout):
    """The session's mode for the report: tmux or a shell, on this Mac or another host."""
    if session is None:
        return {}
    tmux = bool(session.tab and is_tmux(session.tab))
    host = next((it.get("host") for g in layout or [] for it in g.get("items", []) if it.get("id") == session.session_id), None)
    return {"mode": "tmux" if tmux else "shell", "remote": bool(host)}


class Tracer:
    """One browser's part: its keys on their way, the session it opens, a tmux round trip."""

    def __init__(self, client):
        self.client = client
        self.id = next(CLIENTS)
        self.on = False
        self.keys = []           # keys handed to iTerm2, waiting for a frame that shows them
        self.woke = "first"      # what woke the stream for its next read: the key, iTerm2, the poll
        self.read_at, self.read_ms, self.read_woke = 0.0, 0.0, "first"
        self.mode = {}
        self.opened = None
        self.task = None

    @property
    def live(self):
        return self.on and RECORDER.on

    def write(self, rec):
        RECORDER.write("bridge", {"c": self.id, **rec})

    async def handle(self, msg):
        if msg.get("ping") is not None:
            return await self.client.send({"t": "trace", "pong": msg["ping"]})
        if "on" in msg:
            if msg["on"] and not RECORDER.on:
                if msg.get("resume"):            # the page's trace ended while it was away
                    return await self.client.send({"t": "trace", "on": False})
                RECORDER.start()                 # a trace already on is joined, not restarted
            elif not msg["on"]:
                RECORDER.stop()
            self.on = bool(msg["on"])
            if self.on:
                self.mode = mode_of(self.client.session, self.client.hub.layout)
                self.write({"ev": "start", "build": common.BUILD, **self.mode})
                if not self.task:
                    self.task = asyncio.create_task(self.watch())
            return await self.client.send({"t": "trace", "on": self.live, "left": RECORDER.left(),
                                           "file": RECORDER.path.name if RECORDER.path else ""})
        if self.live and isinstance(msg.get("ev"), list):
            for rec in msg["ev"][:MAX_BATCH]:
                rec = clean(rec)
                if rec:
                    RECORDER.write("page", {"c": self.id, **rec, **self.mode})

    def close(self):
        if self.task:
            self.task.cancel()
            self.task = None

    async def watch(self):
        """Every EVERY s: a tmux pane's round trip, keys that never showed, the end told."""
        try:
            probed = time.perf_counter()
            while self.live:
                await asyncio.sleep(EVERY)
                now = time.perf_counter()
                for r in [r for r in self.keys if now - r["t0"] > STALE]:
                    self.keys.remove(r)
                    self.write({"ev": "noecho", "k": r["k"]})
                session = self.client.session
                if session and self.mode.get("mode") == "tmux" and now - probed > PROBE_EVERY:
                    probed = now
                    await self.probe(session)
            await self.client.send_quietly({"t": "trace", "on": False})
        finally:
            self.task = None

    async def probe(self, session):
        try:
            tc = await iterm2.async_get_tmux_connection_by_connection_id(self.client.conn, session.tab.tmux_connection_id)
            if tc:
                t = time.perf_counter()
                await asyncio.wait_for(tc.async_send_command("display-message -p x"), PROBE_TIMEOUT)
                self.write({"ev": "tmux", "ms": ms(t)})
        except Exception as e:     # a probe never stops the trace
            self.write({"ev": "tmux", "error": type(e).__name__})

    # ---------- hooks in the mirror ----------

    def sent(self, msg, t, nbytes):
        if self.live:
            self.write({"ev": "tx", "t": msg.get("t") if isinstance(msg, dict) else "shared", "bytes": nbytes, "ms": ms(t)})

    def handled(self, kind, t):
        if self.live and time.perf_counter() - t > SLOW_MSG and kind != "trace":
            self.write({"ev": "msg", "t": kind if kind in ("sub", "in", "more", "fit", "files", "new") else "other", "ms": ms(t)})

    def key(self, msg):
        """An "in" message: its record, timed step by step, or None."""
        k = msg.get("k")
        if not self.live or not isinstance(k, int):
            return None
        t = time.perf_counter()
        return {"k": k, "t0": t, "m": t}

    def step(self, rec, name, **fields):
        if rec is not None:
            t = time.perf_counter()
            rec[name] = round((t - rec["m"]) * 1000, 2)
            rec["m"] = t
            rec.update(fields)

    def typed(self, rec):
        if rec is not None:
            rec["polls"] = 0
            self.keys.append(rec)

    def woken(self, how):
        if self.woke is None:            # the first cause counts
            self.woke = how

    def read(self, t):
        """A screen read that began at t ended: what woke it and how long it took."""
        if self.live:
            self.read_at, self.read_ms, self.read_woke = t, ms(t), self.woke or "next"
            for r in self.keys:
                if r["m"] <= t:
                    r["polls"] += 1
        self.woke = None

    def opening(self, session, t):
        """The page asked at t for this session."""
        self.keys, self.woke = [], "first"
        if self.live:
            self.mode = mode_of(session, self.client.hub.layout)
            self.opened = {"sid": session.session_id, "t0": t}

    def hist(self, mode, lines, t):
        if self.live:
            self.write({"ev": "hist", "mode": mode, "lines": lines, "ms": ms(t)})

    def frame(self, cursor_moved):
        """Fields for a screen message: the keys it shows (with the cursor moved or its row
        changed: a spinner's frame does not answer a key) and their stages."""
        if not self.live:
            return {}
        if self.opened:
            self.write({"ev": "open", "sid": self.opened["sid"], "ms": ms(self.opened["t0"]), **self.mode})
            self.opened = None
        shown = [r for r in self.keys if r["m"] <= self.read_at]       # typed before this read began
        if not shown:
            return {}
        if not cursor_moved:
            for r in shown:
                r["skip"] = r.get("skip", 0) + 1
            return {}
        now, ks = time.perf_counter(), []
        enc = round((now - self.read_at) * 1000 - self.read_ms, 2)       # diff, history and encoding
        for r in shown:
            b = {"k": r["k"], "fwd": r.get("fwd"), "type": r.get("type"), "via": r.get("via"), "woke": self.read_woke,
                 "wait": round(max(0.0, self.read_at - r["m"]) * 1000, 2), "polls": max(0, r["polls"] - 1),
                 "read": self.read_ms, "bt": round((now - r["t0"]) * 1000, 2), "skip": r.get("skip", 0)}
            ks.append(b)
            self.write({"ev": "key", **b, "enc": enc, **self.mode})
        self.keys = [r for r in self.keys if r not in shown]
        return {"tr": {"ks": ks, "enc": enc}}
