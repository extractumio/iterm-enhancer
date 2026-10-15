// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Reading a context trace (?trace=2, AC-55; the format: docs/web-trace.md) for a check that
// plays it again against a fake bridge: its events and snapshots, iTerm2's rows as the bridge
// sends them, and the browser it came from. Run alone it sums a trace up:
//   node test/tracereplay.mjs <file>

import fs from "node:fs";
import { fileURLToPath } from "node:url";

const EVENT = /^(\d+) ([a-z]+) (\{.*\})$/;
const ROW = /^\|(-?\d+) ([a-z-]+)(?: ([\d+*,]+))?\|(.*)$/;
const WEB = /^:(\d+) (-?[\d.]+)\|(.*)$/;

/** {head, events, state}: events and the state's named lines as {t, ev, o, rows}; a snapshot's
 *  rows ("|": iTerm2's, ":": the page's) go with the named line before them; a line that reads
 *  as none of these is kept as {ev: "raw", line}. */
export function parseTrace(text) {
  const out = { head: null, events: [], state: [] };
  let into = out.events, last = null;
  for (const l of text.split("\n")) {
    if (l.startsWith("#ite-trace ")) { out.head = JSON.parse(l.replace(/^#ite-trace \d+ /, "")); continue; }
    if (l === "#state") { into = out.state; last = null; continue; }
    let m = EVENT.exec(l), o = null;
    try { o = m && JSON.parse(m[3]); } catch { o = null; }
    if (o) { into.push(last = { t: +m[1], ev: m[2], o, rows: [] }); continue; }
    if ((m = ROW.exec(l)) && last) {
      last.rows.push({ i: +m[1], flags: m[2] === "-" ? "" : m[2], runs: m[3] ? m[3].split(",").map(run) : [], text: m[4] });
    } else if ((m = WEB.exec(l)) && last) last.rows.push({ k: +m[1], x: +m[2], text: m[3] });
    else if (l.trim()) into.push({ t: null, ev: "raw", o: {}, rows: [], line: l });
  }
  return out;
}
const run = (s) => { const [, at, n, f] = /^(\d+)\+(\d+)\*(\d+)$/.exec(s).map(Number); return { at, n, f }; };

/** A row of a snapshot as the bridge sends it ({r: [[text, fg, bg, flags]], e}); colors are not
 *  in the trace (a background is "#444"), the text is padded to the runs past its end. */
export function rowData(row) {
  const end = Math.max(row.text.length, ...row.runs.map((r) => r.at + r.n));
  const text = row.text.padEnd(end), cuts = new Set([0, end]);
  for (const r of row.runs) { cuts.add(r.at); cuts.add(r.at + r.n); }
  const at = [...cuts].sort((a, b) => a - b), r = [];
  for (let k = 0; k + 1 < at.length; k++) {
    const f = row.runs.find((x) => x.at <= at[k] && at[k] < x.at + x.n)?.f ?? 0;
    r.push([text.slice(at[k], at[k + 1]), null, f & 4096 ? "#444" : null, f & ~4096]);
  }
  return { r, e: row.flags.includes("e") };
}

const named = (list, ev) => list.filter((x) => x.ev === ev);

/** The bridge's screen message for the state's screen (or a box snapshot's rows). */
export function screenMessage(trace, sid = "S1") {
  const s = named(trace.state, "screen")[0];
  return { t: "screen", sid, full: true, n: s.o.rows, cols: s.o.cols, rows: s.o.rows, alt: s.o.alt, ch: s.rows.map((r) => [r.i, rowData(r)]) };
}

/** The history's last rows the state kept, as a history message's lines. */
export const histLines = (trace) => (named(trace.state, "tail")[0]?.rows ?? []).map(rowData);

/** Playwright's newPage options for the browser the trace came from. */
export function device(trace) {
  const size = named(trace.state, "size")[0]?.o, env = named(trace.state, "env")[0]?.o;
  return { viewport: { width: size.win[0], height: size.win[1] }, deviceScaleFactor: size.dpr, hasTouch: !!env?.touch, isMobile: !!env?.touch };
}

const pct = (xs, p) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : null; };
const stats = (xs) => (xs.length ? `n ${xs.length} p50 ${pct(xs, 0.5)} p90 ${pct(xs, 0.9)} max ${Math.max(...xs)}` : "none");

/** A trace summed up: what is in it, the echo's stages, sessions opened, what went wrong. */
export function summary(trace) {
  const ev = trace.events, out = [`head ${JSON.stringify(trace.head)}`];
  const kinds = {};
  for (const e of ev) kinds[e.ev] = (kinds[e.ev] ?? 0) + 1;
  out.push(`events ${JSON.stringify(kinds)}`, `span ${ev.length ? `${ev[0].t}..${ev.at(-1).t} ms` : "-"}`);
  const echo = named(ev, "echo").map((e) => e.o);
  for (const k of ["total", "rt", "net", "draw", "paint"]) out.push(`echo.${k} ${stats(echo.map((e) => e[k]).filter((x) => x != null))}`);
  for (const k of ["bt", "wait", "read", "type"]) out.push(`echo.br.${k} ${stats(echo.map((e) => e.br?.[k]).filter((x) => x != null))}`);
  out.push(...named(ev, "echo").sort((a, b) => b.o.total - a.o.total).slice(0, 5).map((e) => `slowest echo at ${e.t}: ${JSON.stringify(e.o)}`));
  out.push(...named(ev, "open").map((e) => `open at ${e.t}: ${JSON.stringify(e.o)}`));
  out.push(`rtt ${stats(named(ev, "tick").map((e) => e.o.rtt).filter((x) => x != null))}`);
  for (const k of ["noecho", "slow", "long", "err", "ui"]) out.push(...named(ev, k).map((e) => `${k} at ${e.t}: ${JSON.stringify(e.o)}`));
  out.push(...[...ev, ...trace.state].filter((e) => e.ev === "raw").map((e) => `unreadable line: ${e.line.slice(0, 120)}`));
  const box = named(trace.state, "box")[0], web = named(trace.state, "web")[0];
  if (box) out.push(`box now: ${box.rows.length} iTerm2 rows, ${web?.rows.length ?? 0} page lines; snapshots before: ${named(ev, "box").length}`);
  return out.join("\n");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) { console.error("usage: node test/tracereplay.mjs <trace file>"); process.exit(2); }
  console.log(summary(parseTrace(fs.readFileSync(process.argv[2], "utf8"))));
}
