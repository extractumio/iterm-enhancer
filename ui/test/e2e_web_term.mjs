// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs, on the web app's terminal view: the history is kept in
// blocks that end where a paragraph ends (iOS froze on a selection inside one huge block), the
// newest lines loose, and on a touch screen about `keep` lines while following the output; the
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
const screen = (cols) => new Promise((done) => { view.onScreen({ full: true, n: 3, cols, rows: 3,
  ch: [0, 1, 2].map((i) => [i, line(9000 + i)]) }); requestAnimationFrame(() => requestAnimationFrame(done)); });
// The rules, as a list of broken ones.
const broken = () => {
  const out = [], kids = [...$("hist").children];
  const firstLoose = kids.findIndex((c) => c.classList.contains("ln"));
  if (firstLoose >= 0 && kids.slice(firstLoose).some((c) => !c.classList.contains("ln"))) out.push("a block after loose lines");
  const dom = [...$("hist").querySelectorAll(".ln")];
  if (dom.length !== view.hist.length || dom.some((el, i) => el !== view.hist[i].el)) out.push("lines out of order");
  if (view.hist.some((r, i) => i && r.n !== view.hist[i - 1].n + 1)) out.push("missing lines");
  for (const b of kids.filter((c) => c.classList.contains("blk"))) {
    const l = b.lastElementChild, ends = l.classList.contains("eol") && !l.classList.contains("join");
    if (!ends && b.childElementCount < 400) out.push("a block ends inside a paragraph");
    if (b.childElementCount > 800) out.push("a block of " + b.childElementCount);
  }
  if (view.hist.length && view.hist.at(-1).el.parentNode !== $("hist")) out.push("the last line is in a block");
  return out;
};
window.copied = [];
window.copyByCommand = copyByCommand;
copyButton($("copyfab"), $("term"), (text) => { window.copied.push(text); });
window.t = { view, hist, screen, broken, blocks: () => $("hist").querySelectorAll(".blk").length };
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

  // one line iTerm soft-wrapped 900 times (lines 5000-5899) is cut into blocks of at most 400
  await run(async () => { t.hist("reset", 4900, 1100); });
  s = await state();
  check("AC-52 web: one huge soft-wrapped line is cut into bounded blocks", !s.broken.length, JSON.stringify(s));

  // the Copy button: shown while terminal text is selected, copying it even when the tap
  // cleared the selection and the terminal then caught up (moving the selected line into a
  // block), then clearing it
  const fab = () => run(() => document.getElementById("copyfab").hidden);
  await run(() => { window.picked = [...document.querySelectorAll("#hist > .ln")].at(-3); getSelection().selectAllChildren(window.picked); });
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
