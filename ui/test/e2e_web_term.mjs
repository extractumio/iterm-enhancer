// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs, on the web app's terminal view: each paragraph of the history
// and the screen is a block of its own (iOS 26.4+ selects nothing in a long run of inline lines;
// older iOS froze), the paragraph that goes on into the screen loose, and on a touch screen about `keep` lines while following the output; the
// Copy button for touch screens. The real term.js and touchcopy.js run in a blank page; the
// messages are the ones the bridge sends.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { check } from "./e2e_harness.mjs";

const STATIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bridge/fbbridge/web/static");
const ORIGIN = "http://web.test";
const PAGE = `<!doctype html><link rel=stylesheet href="/style.css">
<body style="margin:0"><main id="term" class="wrap" style="height:600px;width:400px;overflow:auto">
<p id="note" hidden></p><div id="hist"></div><div id="screen"></div></main>
<button id="bottom"></button><p id="paused" hidden></p><button id="copyfab" hidden>Copy</button>
<script type="module">
import { TermView } from "/term.js";
import { copyButton, copyByCommand } from "/touchcopy.js";
import { cellAt } from "/cursor.js";
const $ = (id) => document.getElementById(id);
const view = new TermView({ term: $("term"), hist: $("hist"), screen: $("screen"), note: $("note"),
  bottomBtn: $("bottom"), paused: $("paused"), send: () => {}, keep: 2000 });
const ansi = Array.from({ length: 16 }, () => "#808080");
view.setTheme({ bg: "#000000", fg: "#ffffff", cursor: "#ffffff", cursorText: "#000000", bold: "#ffffff", useBold: false, ansi });
// Line n: mostly paragraphs of 1-6 rows (prose wrapped by the program, re-joined in Wrap),
// tool marks and blank lines; lines 5000-5899 are one line iTerm soft-wrapped 900 times.
const words = "the loader patches require first so every module sees the same cache".split(" ");
const line = (n) => {
  if (n >= 5000 && n < 5900) return { r: [["x".repeat(80), null, null, 0]], e: n === 5899 };
  if (n % 7 === 0) return { r: [["\\u25cf Bash(npm test " + n + ")", null, null, 0]], e: true };
  if (n % 7 === 6) return { r: [], e: true };
  const t = Array.from({ length: 13 }, (_, i) => words[(n + i) % words.length]).join(" ");
  return { r: [[t.slice(0, 78), null, null, 0]], e: true };
};
const hist = (mode, first, count) => view.onHist({ mode, first, oldest: 0, truncated: false,
  lines: Array.from({ length: count }, (_, i) => line(first + i)) });
const screen = (cols, n = 3) => new Promise((done) => { view.onScreen({ full: true, n, cols, rows: n,
  ch: Array.from({ length: n }, (_, i) => [i, line(9000 + i)]) }); requestAnimationFrame(() => requestAnimationFrame(done)); });
const part = (n, ch) => new Promise((done) => { view.onScreen({ full: false, n, cols: 80, rows: n, ch });
  requestAnimationFrame(() => requestAnimationFrame(done)); });
const touch = (type, fingers) => { const e = new Event(type); Object.defineProperty(e, "touches", { value: { length: fingers } }); $("term").dispatchEvent(e); };
const ends = (l) => l.classList.contains("eol") && !l.classList.contains("join");
// The rules, as a list of broken ones.
const broken = () => {
  const out = [], kids = [...$("hist").children];
  const firstLoose = kids.findIndex((c) => c.classList.contains("ln"));
  if (firstLoose >= 0 && kids.slice(firstLoose).some((c) => !c.classList.contains("ln"))) out.push("a block after loose lines");
  const dom = [...$("hist").querySelectorAll(".ln")];
  if (dom.length !== view.hist.length || dom.some((el, i) => el !== view.hist[i].el)) out.push("lines out of order");
  if (view.hist.some((r, i) => i && r.n !== view.hist[i - 1].n + 1)) out.push("missing lines");
  for (const b of kids.filter((c) => c.classList.contains("blk"))) {
    // only a huge soft-wrapped line is cut, every 400 lines (older lines of it loaded before such a block are one more)
    const cut = b.childElementCount === 400 || b.nextElementSibling?.childElementCount === 400;
    if (!ends(b.lastElementChild) && !cut) out.push("a block ends inside a paragraph");
    if ([...b.children].slice(0, -1).some(ends)) out.push("a block holds two paragraphs");
    if (b.childElementCount > 400) out.push("a block of " + b.childElementCount);
  }
  if (view.hist.length && view.hist.at(-1).el.parentNode !== $("hist")) out.push("the last line is in a block");
  // the screen: its first paragraph loose (it goes on from the history's), then one block each
  const rows = view.screen.map((r) => r.el), first = rows.findIndex(ends) + 1 || rows.length;
  if (rows.slice(0, first).some((el) => el.parentNode !== $("screen"))) out.push("the screen's first paragraph is in a block");
  for (const el of rows.slice(first)) {
    const b = el.parentNode;
    if (b === $("screen") || !b.classList.contains("blk")) out.push("a screen line outside a block");
    else if (!ends(b.lastElementChild) || [...b.children].slice(0, -1).some(ends)) out.push("a screen block that is not one paragraph");
  }
  if ([...$("screen").querySelectorAll(".ln")].some((el, i) => el !== rows[i])) out.push("screen lines out of order");
  if ([...$("screen").children].some((c) => c.classList.contains("blk") && !c.childElementCount)) out.push("an empty screen block");
  return out;
};
window.copied = [];
window.copyByCommand = copyByCommand;
copyButton($("copyfab"), $("term"), (text) => { window.copied.push(text); });
window.t = { view, hist, screen, part, line, touch, broken, cellAt, blocks: () => $("hist").querySelectorAll(".blk").length };
window.ready = true;
</script>`;

export async function webTerm(browser) {
  const page = await browser.newPage();
  await page.route(`${ORIGIN}/**`, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/") return route.fulfill({ contentType: "text/html", body: PAGE });
    const f = path.join(STATIC, path.normalize(p));
    if (!f.startsWith(STATIC) || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    return route.fulfill({ contentType: p.endsWith(".css") ? "text/css" : "text/javascript", body: fs.readFileSync(f) });
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${ORIGIN}/`);
  await page.waitForFunction(() => window.ready);
  const run = (fn, arg) => page.evaluate(fn, arg);
  const state = () => run(() => ({ broken: t.broken(), lines: t.view.hist.length, first: t.view.hist[0]?.n, blocks: t.blocks() }));

  await run(async () => { await t.screen(80); t.hist("reset", 3000, 3000); });
  let s = await state();
  check("AC-52 web: 3000 lines of history are kept in blocks that end where a paragraph ends", !s.broken.length && s.blocks >= 20, JSON.stringify(s));

  await run(() => { t.view.toBottom(); t.hist("append", 6000, 20); });
  s = await state();
  check("AC-52 web: following the output, a touch screen keeps about 2000 lines", !s.broken.length && s.lines >= 2000 && s.lines < 2000 + 800 + 20, JSON.stringify(s));

  const first = s.first;
  await run((f) => t.hist("prepend", f - 1000, 1000), first);
  s = await state();
  check("AC-52 web: older lines loaded on scrolling up join the blocks in order", !s.broken.length && s.first === first - 1000, JSON.stringify(s));

  // an older page still on its way: the cap waits, and a page that no longer fits is refused
  const before = s.lines;
  await run(() => { t.view.loading = true; t.view.toBottom(); t.hist("append", 6020, 5); });
  s = await state();
  check("AC-52 web: the cap waits while older lines are on their way", !s.broken.length && s.lines === before + 5, JSON.stringify(s));
  await run((f) => t.hist("prepend", f - 2000, 1000), s.first);
  const gap = await state();
  check("AC-52 web: older lines that would leave a gap are refused", !gap.broken.length && gap.first === s.first && gap.lines === s.lines
    && !(await run(() => t.view.loading)), JSON.stringify(gap));

  await run(() => t.screen(60));
  s = await state();
  check("AC-52 web: a new width re-joins the lines and rebuilds the blocks", !s.broken.length, JSON.stringify(s));

  // the screen: 12 rows, each paragraph a block; a tap finds its row through the blocks
  await run(() => t.screen(80, 12));
  s = await state();
  const blocks = await run(() => document.querySelectorAll("#screen > .blk").length);
  check("AC-52 web: each paragraph of the screen is a block of its own, but the first", !s.broken.length && blocks >= 5, JSON.stringify({ ...s, blocks }));
  const tapped = await run(() => {
    const el = t.view.screen[9].el, r = el.getClientRects()[0];
    el.scrollIntoView();
    const q = el.getClientRects()[0];
    return { inBlock: el.parentNode.classList.contains("blk"), at: t.cellAt(document.getElementById("screen"), q.left + 2, q.top + q.height / 2), had: !!r };
  });
  check("AC-52 web: a tap on a screen row inside a block finds that row", tapped.inBlock && tapped.at?.row === 9, JSON.stringify(tapped));

  // updates of some rows: the blocks stay while the paragraphs do, and follow them when they change
  const changes = await run(async () => {
    const blks = () => [...document.querySelectorAll("#screen > .blk")];
    const out = {}, before = blks();
    await t.part(12, [[9, { r: [["\u25cf Bash(npm run build)", null, null, 0]], e: true }]]);
    out.same = { broken: t.broken(), kept: blks().length === before.length && blks().every((b, i) => b === before[i]) };
    await t.part(12, [[2, { r: [["x".repeat(80), null, null, 0]], e: false }]]);   // row 2 now goes on in row 3
    out.joined = { broken: t.broken(), fewer: blks().length < before.length };
    await t.part(8, []);
    out.shrunk = { broken: t.broken(), rows: document.querySelectorAll("#screen .ln").length };
    await t.part(14, Array.from({ length: 6 }, (_, i) => [8 + i, t.line(9100 + i)]));
    out.grown = { broken: t.broken(), rows: document.querySelectorAll("#screen .ln").length };
    return out;
  });
  check("AC-52 web: a row's new text keeps the screen's blocks", !changes.same.broken.length && changes.same.kept, JSON.stringify(changes.same));
  check("AC-52 web: a row whose line end goes away joins its paragraph's block", !changes.joined.broken.length && changes.joined.fewer, JSON.stringify(changes.joined));
  check("AC-52 web: fewer and more screen rows keep one block per paragraph",
    !changes.shrunk.broken.length && changes.shrunk.rows === 8 && !changes.grown.broken.length && changes.grown.rows === 14, JSON.stringify(changes));

  // a finger lifted waits 400 ms before updates go on: not when another touch began meanwhile,
  // nor while a second finger is still down
  const held = await run(async () => {
    const wait = () => new Promise((r) => setTimeout(r, 500));
    t.touch("touchstart", 1); t.touch("touchend", 0); t.touch("touchstart", 1); await wait();
    const next = t.view.touching;
    t.touch("touchstart", 2); t.touch("touchend", 1); await wait();
    const other = t.view.touching;
    t.touch("touchend", 0); await wait();
    return { next, other, lifted: t.view.touching };
  });
  check("AC-52 web: updates stay paused for a new touch and a finger still down, and go on after the lift",
    held.next && held.other && !held.lifted, JSON.stringify(held));

  // one line iTerm soft-wrapped 900 times (lines 5000-5899) is cut into blocks of at most 400
  await run(async () => { t.hist("reset", 4900, 1100); });
  s = await state();
  check("AC-52 web: one huge soft-wrapped line is cut into bounded blocks", !s.broken.length, JSON.stringify(s));
  // older lines that end inside such a line: a block of their own when the next one is full
  await run(() => { t.hist("reset", 5500, 500); t.hist("prepend", 5200, 300); });
  s = await state();
  check("AC-52 web: older lines of a huge soft-wrapped line keep blocks bounded", !s.broken.length && s.first === 5200, JSON.stringify(s));

  // the Copy button: shown while terminal text is selected, copying it even when the tap
  // cleared the selection and the terminal then caught up (moving the selected line into a
  // block), then clearing it
  const fab = () => run(() => document.getElementById("copyfab").hidden);
  await run(() => { window.picked = [...document.querySelectorAll("#hist > .ln")].at(-1); getSelection().selectAllChildren(window.picked); });
  await page.waitForTimeout(100);
  const word = await run(() => String(getSelection()).trimEnd());
  check("AC-52 web: a Copy button appears while terminal text is selected", !(await fab()) && word.length > 0);
  await run(() => { t.hist("append", 6000, 150); });                     // queued: the terminal is paused
  await run(() => getSelection().removeAllRanges());                     // what a tap on it does on iOS
  await page.waitForTimeout(100);
  check("AC-52 web: (the terminal caught up and moved the selected line into a block)", await run(() => window.picked.parentElement.classList.contains("blk")));
  await run(() => document.getElementById("copyfab").click());
  await page.waitForTimeout(100);
  const copied = await run(() => window.copied);
  check("AC-52 web: … and copies the selection the tap cleared", copied.length === 1 && copied[0] === word, JSON.stringify(copied));
  await page.waitForTimeout(800);
  check("AC-52 web: … then the selection is gone and so is the button", await fab() && !(await run(() => String(getSelection()))));
  await run(() => { getSelection().selectAllChildren(document.getElementById("note")); });
  await page.waitForTimeout(800);
  check("AC-52 web: a selection outside the terminal shows no Copy button", await fab());
  // without HTTPS (a Tailscale address) the button copies with the older copy command
  const byCommand = await run(() => {
    let got = null;
    const grab = () => { got = String(getSelection()); };
    document.addEventListener("copy", grab);
    const ok = window.copyByCommand("line one\n  line two");
    document.removeEventListener("copy", grab);
    return { ok, got, insecure: !window.isSecureContext, left: String(getSelection()) };
  });
  check("AC-52 web: without HTTPS the text is copied by the older copy command, line ends kept",
    byCommand.insecure && byCommand.got === "line one\n  line two" && byCommand.left === "", JSON.stringify(byCommand));
  check("AC-52 web: no page errors", !errors.length, errors.join("; "));
  await page.close();
}
