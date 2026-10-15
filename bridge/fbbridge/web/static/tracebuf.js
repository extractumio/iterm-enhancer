// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Context trace (AC-55). With ?trace=2 the page keeps in its memory, as a ring, the last events
// of its work: keys and their echo on the page, sessions opened to their first frame and input,
// clicks and taps, the keyboard field's input, focus, sizes, scrolls, selections, the messages
// to and from the bridge, notices, errors and stalls; and snapshots of a coding agent's input box
// as iTerm2 has it and as the page lays it out. The trace button (left of ⓘ) copies them with
// the page's state now as text for a coding agent to read (docs/web-trace.md). Unlike ?trace=1
// it holds what is typed and shown, and it leaves the page only by the user's copy.
import { cellAt } from "./cursor.js";
import { boxShot, boxSig, desc, shot, sizes } from "./traceshot.js";

export const ON = new URLSearchParams(globalThis.location?.search ?? "").get("trace") === "2";
const EVENTS = 640;            // event lines kept
const EVENT_CHARS = 400;       // an event line's most characters
const SHOTS = 3;               // input box snapshots kept (each changed one)
const LINE = 4000;             // any line's most characters
const LINES = 1000;            // the copy's most lines
const TOTAL = 120000;          // the copy's most characters (the oldest events go first)
const EVERY = 5000;            // ms between ticks: counts and a round trip
const ECHO_WAIT = 5000;        // a key with no screen message by then: noecho
const OPEN_WAIT = 15000;       // a session opened with no input by then: told so
const SLOW_DRAW = 50;          // ms of drawing worth an event
const JANK = 100;              // ms between two frames that counts as a stall
const DATA = 40;               // characters of a key's or a text's content kept

const now = () => performance.now();
const ms = (v) => Math.round(v * 10) / 10;
const cut = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const events = [], shots = [];
let dropped = 0, ctx = null, bytes = 0;

/** o as JSON of at most max characters: its longest texts shortened, then its last fields
 *  dropped, with cut: true; the line stays JSON. */
export function fit(o, max) {
  let j = JSON.stringify(o);
  if (j.length <= max) return j;
  o = { ...o, cut: true };
  for (let n = 0; (j = JSON.stringify(o)).length > max && n < 100; n++) {
    const [k, v] = Object.entries(o).filter(([, x]) => typeof x === "string" && x.length > 20).sort((a, b) => b[1].length - a[1].length)[0] ?? [];
    if (k) o[k] = cut(v, Math.max(20, v.length - (j.length - max) - 1));
    else { const keys = Object.keys(o).filter((x) => x !== "cut"); if (!keys.length) break; delete o[keys.at(-1)]; }
  }
  return j.length <= max ? j : '{"cut":true}';
}

/** An event line: "<ms since the page opened> <name> <json>". */
export function rec(ev, o = {}) {
  if (!ON) return;
  const head = `${Math.round(now())} ${ev} `;
  events.push(head + fit(o, EVENT_CHARS - head.length));
  if (events.length > EVENTS) { events.shift(); dropped++; }
}

// ---------- keys, their echo, sessions opened (fed by trace.js's hooks) ----------
let waiting = [], echo = null, open = null, kn = 0, staged = false;   // staged: the bridge times keys too
const counts = { rx: {}, tx: 0, draws: 0, dmax: 0, jank: 0, jmax: 0 };

/** A key the page sends: how it came, what it is, from its event to the send. Its number k
 *  goes with it, for the bridge's stages in the screen message that shows it (trace.py). */
export function key(data, path, e) {
  if (!ON) return {};
  const t1 = now(), t = e?.timeStamp, t0 = !e?.repeat && t > 0 && t <= t1 && t1 - t < 60000 ? t : t1;
  waiting.push({ k: ++kn, t0, t1 });
  const d = repeats(data);
  rec("key", { k: kn, p: path, d: d.slice(0, DATA), n: d.length > DATA ? data.length : undefined, in: ms(t1 - t0) });
  return { k: kn };
}
/** The WebSocket is open: the bridge is asked for its key stages (nothing is written there). */
export function connected(send) { if (ON) send({ t: "trace", stages: true }); }
// a run of the same escape sequence (a tap's arrows) as "SEQ×n"
const repeats = (data) => data.replace(/(\x1b[[O][\d;]*[A-Za-z~])\1{2,}/g, (run, seq) => `${seq}×${run.length / seq.length}`);
/** Every message the page sends (keys are key's). */
export function sent(m) {
  if (!ON || m.t === "in" || (m.t === "trace" && m.ping != null)) return;
  counts.tx++;
  rec("tx", brief(m));
}
/** The size of the message about to be parsed. */
export function size(n) { bytes = n; }

/** Every message from the bridge the page applies. */
export function rx(m) {
  if (!ON) return;
  const c = (counts.rx[m.t] ??= [0, 0]);
  c[0]++; c[1] += bytes;
  if (m.t === "screen") {
    // the keys it shows: those the bridge names (a frame where the cursor moved), else any sent before it
    const ks = m.tr?.ks ?? [], shown = staged ? waiting.filter((w) => ks.some((b) => b.k === w.k)) : waiting;
    if (shown.length) {
      waiting = waiting.filter((w) => !shown.includes(w));
      if (echo) echo.keys.push(...shown);
      else echo = { keys: shown, at: now(), ch: m.ch?.length ?? 0, full: !!m.full || undefined, cur: cursorIn(m), br: ks.find((b) => b.k === shown[0].k), enc: m.tr?.enc };
    }
    if (open && open.scr == null) open.scr = now() - open.t0;
    return;
  }
  if (m.t === "trace") { if (typeof m.pong === "string") pong(m.pong); if ("stages" in m) staged = m.stages === true; return; }
  if (m.t === "hist") {
    rec("hist", { mode: m.mode, first: m.first, n: m.lines?.length, oldest: m.oldest, kb: ms(bytes / 1024) });
    if (open && m.mode === "reset" && open.hist == null) Object.assign(open, { hist: now() - open.t0, lines: m.lines?.length, kb: ms(bytes / 1024) });
    return;
  }
  rec("rx", brief(m));
}
// the cursor's [row, column] in a screen message's changed rows
function cursorIn(m) {
  for (const [i, data] of m.ch ?? []) {
    let col = 0;
    for (const [t, , , f] of data?.r ?? []) { if (f & 256) return [i, col]; col += Array.isArray(t) ? t.join("").length : t.length; }
  }
  return undefined;
}
// what the user wrote in a field other than the terminal's: kept by its length
const WRITTEN = new Set(["note", "name", "q", "password"]);
// a message's kind and its small fields; long ones by their length
function brief(m) {
  const o = {};
  for (const [k, v] of Object.entries(m)) {
    if (k === "data") continue;
    if (WRITTEN.has(k) && typeof v === "string") { o[k] = `(${v.length})`; continue; }
    if (Array.isArray(v)) o[k] = `[${v.length}]`;
    else if (typeof v === "string") o[k] = cut(v, 60);
    else if (v && typeof v === "object") o[k] = "{…}";
    else o[k] = v;
  }
  return o;
}

/** The user picked a session: timed to its history, first screen, first frame and input. */
export function opening(sid) {
  if (!ON) return;
  if (open) rec("open", { ...openFields(open), left: true });
  open = { sid, t0: now() };
  rec("pick", { sid });
}
const openFields = (o) => ({ sid: o.sid, hist: o.hist && ms(o.hist), lines: o.lines, kb: o.kb, drawn: o.drawn && ms(o.drawn), scr: o.scr && ms(o.scr), paint: o.paint && ms(o.paint) });

/** Drawing took this long (TermView.flush, onHist). */
export function drew(what, took, rows) {
  if (!ON) return;
  counts.draws++;
  counts.dmax = Math.max(counts.dmax, took);
  if (what === "hist" && open?.hist != null && open.drawn == null) open.drawn = now() - open.t0;   // the history drawn
  if (took > SLOW_DRAW) rec("slow", { what, ms: ms(took), rows });
}

/** The terminal drew what came: an echo's and an opened session's paint is the next frame. */
export function drawn() {
  if (!ON) return;
  const t3 = now(), e = echo;
  echo = null;
  requestAnimationFrame(() => {
    const t4 = now();
    if (e) {
      const rt = e.at - e.keys[0].t1, b = e.br;
      rec("echo", { keys: e.keys.length, k: e.keys[0].k, rt: ms(rt), draw: ms(t3 - e.at), paint: ms(t4 - t3), total: ms(t4 - e.keys[0].t0), ch: e.ch, full: e.full, cur: e.cur,
        ...(b ? { br: { fwd: b.fwd, type: b.type, via: b.via, wait: b.wait, polls: b.polls, woke: b.woke, read: b.read, enc: e.enc, bt: b.bt, skip: b.skip || undefined }, net: ms(rt - b.bt) } : {}) });
    }
    if (open?.scr != null && open.drawn != null) {
      open.paint ??= t4 - open.t0;
      const box = ctx?.view.box, cur = document.querySelector("#screen .cur");
      if (box || cur) { rec("open", { ...openFields(open), ready: ms(t4 - open.t0), box: !!box, keys: document.activeElement?.id === "kbd" }); open = null; }
    }
    boxLater();
  });
}

// a snapshot's named lines get the time; its rows ("|" iTerm2's, ":" the page's) do not
const stamped = (t) => (l) => (l[0] === "|" || l[0] === ":" ? l : `${t} ${l}`);

// ---------- the input box's snapshots, when it changed ----------
// (after 300 ms without a frame, or 2 s into a run of them: measuring costs while typing)
let boxTimer = 0, boxFirst = 0, lastSig = "";
function boxLater() {
  if (!ctx?.view.box) return;
  if (!boxTimer) boxFirst = now();
  clearTimeout(boxTimer);
  boxTimer = setTimeout(() => {
    boxTimer = 0;
    const sig = boxSig(ctx.view);
    if (!sig || sig === lastSig) return;
    lastSig = sig;
    shots.push(boxShot(ctx.view).map(stamped(Math.round(now()))));
    if (shots.length > SHOTS) shots.shift();
  }, now() - boxFirst > 2000 ? 0 : 300);
}

// ---------- the page's own events ----------
let pings = 0;
const sentPings = new Map();
function pong(id) { const t = sentPings.get(id); sentPings.delete(id); if (t != null) counts.rtt = ms(now() - t); }

function later(fn, wait) { let timer = 0; return (...a) => { clearTimeout(timer); timer = setTimeout(() => fn(...a), wait); }; }
const field = (el) => el?.closest?.("input, textarea, select, [contenteditable]");
const mods = (e) => (e.metaKey ? "⌘" : "") + (e.ctrlKey ? "⌃" : "") + (e.altKey ? "⌥" : "") + (e.shiftKey ? "⇧" : "");

function listen(send) {
  const kbd = document.getElementById("kbd"), term = document.getElementById("term");
  const down = new Map();
  addEventListener("pointerdown", (e) => {
    const cell = cellAt(document.getElementById("screen"), e.clientX, e.clientY);
    // the keyboard field lies over the input on a touch screen: what is under it too
    const under = e.target === kbd ? document.elementsFromPoint(e.clientX, e.clientY).find((el) => el !== kbd) : null;
    down.set(e.pointerId, { t: now(), x: e.clientX, y: e.clientY, on: desc(e.target), under: under ? desc(under) : undefined, cell: cell ? [cell.row, cell.col] : undefined });
  }, { capture: true, passive: true });
  const up = (e) => {
    const d = down.get(e.pointerId);
    down.delete(e.pointerId);
    if (!d) return;
    const took = now() - d.t, dx = Math.round(e.clientX - d.x), dy = Math.round(e.clientY - d.y);
    const k = e.type === "pointercancel" ? "cancel" : Math.hypot(dx, dy) > 10 ? "drag" : took > 500 ? "long" : "tap";
    rec("ptr", { k, pt: e.pointerType, b: e.button || undefined, x: Math.round(d.x), y: Math.round(d.y), dx: dx || undefined, dy: dy || undefined,
      ms: Math.round(took), on: d.on, under: d.under, cell: d.cell, m: mods(e) || undefined });
  };
  addEventListener("pointerup", up, { capture: true, passive: true });
  addEventListener("pointercancel", up, { capture: true, passive: true });
  addEventListener("click", (e) => {
    const b = e.target.closest?.("button, a, [role=tab], [role=menuitem], .item, summary, label");
    if (b) rec("click", { on: desc(b), label: cut((b.getAttribute("aria-label") || b.textContent || "").trim(), 30) || undefined });
  }, { capture: true, passive: true });
  // keys: in the terminal's field as pressed; in another field never what they write
  addEventListener("keydown", (e) => {
    const f = field(e.target), mine = e.target === kbd;
    rec("kd", { k: !mine && f && e.key.length === 1 ? "·" : e.key, c: mine ? e.code : undefined, m: mods(e) || undefined, r: e.repeat || undefined,
      ime: e.isComposing || undefined, on: mine ? undefined : desc(e.target) });
  }, { capture: true, passive: true });
  kbd?.addEventListener("beforeinput", (e) => rec("inp", { it: e.inputType, d: e.data?.slice(0, DATA), n: (e.data?.length ?? 0) > DATA ? e.data.length : undefined, ime: e.isComposing || undefined }));
  for (const t of ["compositionstart", "compositionend"]) kbd?.addEventListener(t, (e) => rec("comp", { s: t.slice(11), d: e.data?.slice(0, DATA) }));
  addEventListener("focusin", (e) => rec("foc", { on: desc(e.target) }));
  const vis = () => rec("vis", { hidden: document.hidden, focus: document.hasFocus() });
  document.addEventListener("visibilitychange", vis);
  addEventListener("focus", vis);
  addEventListener("blur", vis);
  addEventListener("pageshow", (e) => rec("show", { persisted: e.persisted }));
  const vp = later(() => ctx && rec("vp", sizes(ctx.view)), 250);
  addEventListener("resize", vp);
  window.visualViewport?.addEventListener("resize", vp);
  window.visualViewport?.addEventListener("scroll", vp);
  term?.addEventListener("scroll", later(() => rec("scroll", { top: Math.round(term.scrollTop), max: term.scrollHeight - term.clientHeight, bottom: ctx?.view.atBottom() }), 300), { passive: true });
  let selWas = "";
  document.addEventListener("selectionchange", later(() => {
    const s = String(getSelection() ?? "");
    if (s === selWas) return;
    selWas = s;
    const other = field(document.activeElement) && document.activeElement !== kbd;   // a selection in another field: its length
    rec("sel", { n: s.length, d: s && !other ? cut(s, DATA) : undefined, in: s ? desc(other ? document.activeElement : getSelection().anchorNode?.parentElement) : undefined });
  }, 400));
  // a stack's addresses as file names: the page's own address is the Mac's
  const stack = (s) => String(s ?? "").replace(/\b(?:https?|wss?):\/\/[^\s)]*\//g, "");
  addEventListener("error", (e) => rec("err", { msg: String(e.message), at: e.filename ? `${e.filename.split("/").pop()}:${e.lineno}:${e.colno}` : undefined, stack: stack(e.error?.stack) }));
  addEventListener("unhandledrejection", (e) => rec("err", { msg: String(e.reason?.message ?? e.reason), stack: stack(e.reason?.stack), promise: true }));
  try { new PerformanceObserver((l) => { for (const x of l.getEntries()) rec("long", { ms: Math.round(x.duration) }); }).observe({ type: "longtask" }); } catch { /* Safari: no long tasks */ }
  // notices the page shows: their text when they appear or change
  const seen = new Map();
  const notice = new MutationObserver(() => {
    for (const id of ["toast", "status", "drops", "stall", "paused", "note", "mbwhy", "viewnote"]) {
      const el = document.getElementById(id), text = el && !el.hidden ? el.textContent.trim() : "";
      if (text !== (seen.get(id) ?? "")) { seen.set(id, text); if (text) rec("ui", { el: `#${id}`, text: cut(text, 120) }); }
    }
  });
  for (const id of ["toast", "status", "drops", "stall", "paused", "note", "mbwhy", "viewnote"]) {
    const el = document.getElementById(id);
    if (el) notice.observe(el, { attributes: true, attributeFilter: ["hidden"], childList: true, characterData: true, subtree: true });
  }
  // stalls between frames, counts, and a round trip to the bridge, every EVERY ms
  let prev = now();
  const loop = () => { const t = now(), gap = t - prev; prev = t; if (gap > JANK && !document.hidden) { counts.jank++; counts.jmax = Math.max(counts.jmax, gap); } requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
  setInterval(() => {
    const t = now();
    const old = waiting.filter((w) => t - w.t1 > ECHO_WAIT);
    if (old.length) { rec("noecho", { keys: old.length, k: old[0].k, ms: Math.round(t - old[0].t1) }); waiting = waiting.filter((w) => !old.includes(w)); }
    if (open && t - open.t0 > OPEN_WAIT) { rec("open", { ...openFields(open), ready: null }); open = null; }
    if (document.hidden) return;
    const busy = counts.tx || counts.draws || Object.keys(counts.rx).length;
    if (busy) rec("tick", { rx: counts.rx, tx: counts.tx, draws: counts.draws, dmax: ms(counts.dmax), jank: counts.jank ? [counts.jank, Math.round(counts.jmax)] : undefined,
      rtt: counts.rtt, dom: document.querySelectorAll("#term .ln").length, heap: performance.memory ? Math.round(performance.memory.usedJSHeapSize / 1048576) : undefined });
    Object.assign(counts, { rx: {}, tx: 0, draws: 0, dmax: 0, jank: 0, jmax: 0, rtt: undefined });
    const id = `x${++pings}`;
    sentPings.set(id, t);
    if (sentPings.size > 20) sentPings.delete(sentPings.keys().next().value);
    send({ t: "trace", ping: id });
  }, EVERY);
}

// ---------- the button and the copy ----------

/** The page is set up: ctx {view, st, bookmarks, widgets, mode(), findItem, copy(text), send}. */
export function init(c) {
  if (!ON) return;
  ctx = c;
  rec("load", { build: document.querySelector('meta[name="build"]')?.content, url: location.pathname + location.search, win: [innerWidth, innerHeight], dpr: devicePixelRatio });
  listen(c.send);
  document.body.classList.add("ctxtrace");
  const b = document.createElement("button");
  Object.assign(b, { type: "button", id: "tracefab", textContent: "trace", title: "Copy the trace: the page's last events and its state, for a coding agent" });
  b.setAttribute("aria-label", "Copy the trace");
  b.addEventListener("mousedown", (e) => e.preventDefault());         // the terminal keeps the keys
  b.addEventListener("click", () => c.copy(text()));
  const fabs = document.getElementById("fabs");
  fabs?.insertBefore(b, document.getElementById("footfab"));
}

/** The copy: a header, the events and the input box's snapshots by time, the state now; at
 *  most LINES lines of at most LINE characters and TOTAL in all (the oldest events go first). */
export function text() {
  const t = Math.round(now());
  rec("copy", {});
  let state = [];
  try { state = shot(ctx).map(stamped(t)); } catch (e) { state = [`${t} err ${JSON.stringify({ msg: `snapshot: ${e.message}` })}`]; }
  state = state.map(fitLine);
  const time = (g) => parseInt(g[0], 10), size = (g) => g.reduce((n, l) => n + Math.min(l.length, LINE) + 1, 0);
  const groups = [...events.map((l) => [l]), ...shots].sort((a, b) => time(a) - time(b));
  let room = Math.max(0, LINES - 2 - state.length), chars = TOTAL - 200 - size(state), from = groups.length;
  while (from > 0 && groups[from - 1].length <= room && size(groups[from - 1]) <= chars) { room -= groups[from - 1].length; chars -= size(groups[--from]); }
  const kept = groups.slice(from), ev = kept.filter((g) => g.length === 1).length;
  const head = `#ite-trace 1 ${JSON.stringify({ at: new Date().toISOString(), up: t, events: ev, dropped: dropped + events.length - ev, doc: "docs/web-trace.md" })}`;
  const out = [head, ...kept.flat(), "#state", ...state].slice(0, LINES).map((l) => cut(l, LINE));
  return `${out.join("\n")}\n`;
}
// a named line within LINE characters, still JSON; rows are cut as they are
function fitLine(l) {
  const m = /^(\d+ [a-z]+ )(\{.*\})$/.exec(l);
  return m && l.length > LINE ? m[1] + fit(JSON.parse(m[2]), LINE - m[1].length) : l;
}
