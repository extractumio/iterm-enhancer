// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Latency trace (AC-55). With ?trace=1 the page numbers each key it sends (k) and times its way:
// the event to the send, the send to the screen message that answers it (the bridge names the
// keys and its own stages in it), the parse, the drawing and the next frame; also a session
// opened, pings and stalled frames. Records go to the bridge every 2 s, which writes them with
// its own to a log on the Mac (trace.py). Nothing typed or shown is recorded: kinds and times.
const ON = new URLSearchParams(globalThis.location?.search ?? "").get("trace") === "1";
const EVERY = 2000;             // ms between batches, pings and checks
const STALE = 10000;            // a key with no echo by then is noted without one
const JANK = 100;               // ms between two frames that counts as a stall
const SLOW_DRAW = 16;           // ms of drawing worth a record

let link = null;                // {send, ws}
let started = false, live = false, k = 0, n = 0, compStart = null, parsed = null, open = null;
let batch = [], echoes = [], endsAt = 0, file = "", badge = null;
const keys = new Map(), pings = new Map(), drawnKeys = [];
const now = () => performance.now();
const push = (rec) => { if (live) batch.push(rec); };
// an event's own time, so a page busy before its handler ran is counted; else now
// (a repeated key carries its first press's time on iOS)
const stamp = (e) => { const t = e?.timeStamp, at = now(); return !e?.repeat && t > 0 && t <= at && at - t < 60000 ? t : at; };

/** What a key is, never what it says. */
export function keyKind(data) {
  if (data === "\r") return "enter";
  if (data === "\x7f" || data === "\b") return "backspace";
  if (data === "\t") return "tab";
  if (data === "\x1b") return "esc";
  if (data.startsWith("\x1b")) return "seq";
  if (data.length === 1 && data < " ") return "ctrl";
  return [...data].length === 1 ? "char" : data ? "text" : "none";
}

/** The WebSocket is open: ask the bridge to record (a reconnect goes on with the same file). */
export function connected(send, ws) {
  if (!ON) return;
  link = { send, ws };
  send({ t: "trace", on: true, resume: started });
  started = true;
}

/** A composition (iOS's predictive text, an IME) began: its text comes only at its end. */
export function composing(e) { if (live) compStart = stamp(e); }

/** Fields for an "in" message: its key number, and the record of its way so far. */
export function keyFields(data, path, e) {
  if (!live) return {};
  const t1 = now(), t0 = e ? stamp(e) : t1;
  k += 1;
  keys.set(k, { ev: "echo", k, path, cls: keyKind(data), n: data.length, comp: compStart == null ? undefined : t1 - compStart,
    in: t1 - t0, buf: link?.ws.bufferedAmount ?? 0, t0, t1 });
  if (path === "compose") compStart = null;
  return { k };
}

/** A message from the bridge, parsed and timed. */
export function parse(text) {
  if (!live) return JSON.parse(text);
  const a = now(), m = JSON.parse(text);
  parsed = { at: a, ms: now() - a, bytes: text.length };
  return m;
}

/** A message the page will apply: keys it answers, or a session's first screen. */
export function received(m) {
  if (!live || m.t !== "screen") return;
  for (const b of m.tr?.ks ?? []) {
    const r = keys.get(b.k);
    if (!r) continue;
    keys.delete(b.k);
    Object.assign(r, b, { rt: (parsed?.at ?? now()) - r.t1, parse: parsed?.ms, enc: m.tr.enc });
    drawnKeys.push(r);
  }
  if (open && m.sid === open.sid && open.first == null) { open.first = (parsed?.at ?? now()) - open.t0; open.bytes = parsed?.bytes; }
}

/** The terminal drew what came: the keys' and an opened session's next frame is their paint. */
export function drawn() {
  if (!live) return;
  const t3 = now(), done = drawnKeys.splice(0), o = open?.first != null ? open : null;
  if (o) open = null;
  if (!done.length && !o) return;
  requestAnimationFrame(() => {
    const t4 = now();
    for (const r of done) {
      r.draw = t3 - (r.t1 + r.rt);
      r.paint = t4 - t3;
      r.total = t4 - r.t0;
      echoes.push(r.total);
      if (echoes.length > 400) echoes.splice(0, 200);    // the badge looks at the last 200
      const { t0, t1, ...rec } = r;
      push(rec);
    }
    if (o) push({ ev: "open", sid: o.sid, first: o.first, paint: t4 - o.t0, bytes: o.bytes });
  });
}

/** The user picked a session. */
export function opening(sid) { if (live) open = { sid, t0: now(), first: null }; }

/** Drawing that takes longer than a frame, by what drew. */
export function watch(view) {
  if (!ON) return;
  for (const [name, what] of [["flush", "screen"], ["onHist", "hist"]]) {
    const f = view[name];
    view[name] = function (...a) {
      const s = now(), r = f.apply(this, a), ms = now() - s;
      if (ms > SLOW_DRAW) push({ ev: "draw", what, ms, rows: this.screen.length + this.hist.length });
      return r;
    };
  }
}

/** The bridge's answer: on or off, and pongs. */
export function onMessage(m) {
  if (m.pong != null) {
    const t = pings.get(m.pong);
    pings.delete(m.pong);
    if (t != null) push({ ev: "ping", n: m.pong, rtt: now() - t });
    return;
  }
  if (m.on && !live) begin();
  if (m.on) { endsAt = now() + m.left * 1000; file = m.file || file; }
  else if (live) end();
  showBadge();
}

let tick = 0, frame = 0, jank = { count: 0, max: 0 };
function begin() {
  live = true;
  tick = setInterval(every, EVERY);
  let prev = now();
  const loop = () => {
    const t = now(), gap = t - prev;
    prev = t;
    if (gap > JANK) { jank.count += 1; jank.max = Math.max(jank.max, gap); }
    frame = requestAnimationFrame(loop);
  };
  frame = requestAnimationFrame(loop);
}
function end() {
  every();
  live = false;
  clearInterval(tick);
  cancelAnimationFrame(frame);
  keys.clear(); pings.clear(); drawnKeys.length = 0; open = null;
}
function every() {
  const t = now();
  for (const [key, r] of keys) {
    if (t - r.t1 < STALE) continue;
    keys.delete(key);
    push({ ev: "noecho", k: r.k, path: r.path, cls: r.cls, n: r.n });
  }
  if (jank.count && !document.hidden) push({ ev: "jank", ...jank });
  jank = { count: 0, max: 0 };
  n += 1;
  pings.set(n, t);
  link?.send({ t: "trace", ping: n });
  while (batch.length) link?.send({ t: "trace", ev: batch.splice(0, 500) });
}

// A small badge, redrawn once a second: on, the minutes left, the last and the median echo.
let badgeTimer = 0;
function showBadge() {
  if (!badge) {
    badge = document.createElement("button");
    badge.type = "button";
    badge.id = "tracebadge";
    badge.title = "Latency trace: tap to end it";
    badge.addEventListener("click", () => { if (live) link?.send({ t: "trace", on: false }); else badge.hidden = true; });
    document.body.append(badge);
  }
  if (live && !badgeTimer) { badge.hidden = false; badgeTimer = setInterval(drawBadge, 1000); }   // a later trace too
  drawBadge();
}
function drawBadge() {
  if (!live) {
    badge.textContent = `trace ended${file ? ` · ${file}` : ""}`;
    clearInterval(badgeTimer);
    badgeTimer = 0;
    return;
  }
  const sorted = echoes.slice(-200).sort((a, b) => a - b);
  const med = sorted.length ? Math.round(sorted[sorted.length >> 1]) : "–", lastMs = echoes.length ? Math.round(echoes.at(-1)) : "–";
  const left = Math.max(0, (endsAt - now()) / 1000), m = Math.floor(left / 60), s = String(Math.floor(left % 60)).padStart(2, "0");
  badge.textContent = `● trace ${m}:${s} · echo ${lastMs} ms · p50 ${med}`;
}
