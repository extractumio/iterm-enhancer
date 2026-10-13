// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs: ↑, ↓ and a click in a coding agent's input box move by the
// lines the page draws, not by iTerm's rows. The fake bridge plays Claude Code's input as measured
// in iTerm2: rows of cols - 2 characters (prompt included) wrapped at a space, ← and → a character
// a step (the space of a wrap and a line end are one), ↑ on its first row recalls history. And
// Claude Code's real rows, recorded in iTerm2 at 60 columns while a text was typed a character at
// a time (claude-input-60.json: the text, each frame's row starts and cursor): every frame is one
// line on the page, and the page counts the cursor where it is in the text. A menu Claude Code
// draws between rules (its questions) keeps its own ↑ and ↓.
// Alone: node test/e2e_web_boxnav.mjs.

import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { check, cleanup, results } from "./e2e_harness.mjs";
import { open } from "./e2e_web_widgets.mjs";

const COLS = 60, W = COLS - 4;                         // the text's room in a row, after the 2-cell prompt
const RULE = "─".repeat(COLS);
const line = (text) => ({ r: [[text, null, null, 0]], e: true });

/** Claude Code's rows of `text`: [{start, text}] (start: the offset of the row's first character). */
function rowsOf(text) {
  const rows = [];
  let at = 0;
  for (const part of text.split("\n")) {
    let rest = part, start = at;
    while (rest.length > W) {
      let cut = rest.lastIndexOf(" ", W);
      const glue = cut <= 0;
      if (glue) cut = W;
      rows.push({ start, text: rest.slice(0, cut) });
      start += cut + (glue ? 0 : 1);
      rest = rest.slice(cut + (glue ? 0 : 1));
    }
    rows.push({ start, text: rest });
    at += part.length + 1;
  }
  return rows;
}

/** The screen with the input box holding `text` and Claude Code's cursor at offset `at`; parked:
 *  the terminal's cursor left on the row after the footer, as Claude Code's own redraws leave it. */
function screen(text, at, parked = false) {
  const rows = rowsOf(text);
  let cr = rows.length - 1;
  while (cr > 0 && rows[cr].start > at) cr--;
  const box = rows.map((r, i) => {
    const t = (i ? "  " : "❯ ") + r.text;
    if (i !== cr) return line(t);
    const x = 2 + at - r.start;
    if (parked) return line(t);
    return x < t.length
      ? { r: [[t.slice(0, x), null, null, 0], [t[x], null, null, 256], ...(t.slice(x + 1) ? [[t.slice(x + 1), null, null, 0]] : [])], e: true }
      : { r: [[t, null, null, 0], [" ", null, null, 256]], e: true };
  });
  return [line("⏺ Done."), line(""), line(RULE), ...box, line(RULE), line("  ~/app main"), line("  ⏵⏵ accept edits on"),
    ...(parked ? [{ r: [[" ", null, null, 256]], e: true }] : [])];
}

const TEXT = "the problem with wrapping stays when the width of the original iTerm2 window differs from the browser's: "
  + "when the cursor reaches the width of the iTerm2 window the browser's input box starts a new line";

export async function webBoxNav(browser, name = "chromium") {
  const at = `AC-52 web app (${name}), moving in a coding agent's input by the page's lines:`;
  const cc = { text: TEXT, at: TEXT.length, history: 0, delay: 20 };
  let say = null;
  const draw = () => {
    const rows = cc.menu ?? screen(cc.text, cc.at, cc.parked);
    say({ t: "screen", sid: "S1", full: true, n: rows.length, cols: COLS, rows: 30, ch: rows.map((r, i) => [i, r]) });
  };
  const hook = (msg) => {
    if (msg.t !== "in") return;
    cc.parked = false;                             // a key puts the cursor back on the caret (measured)
    for (const k of msg.data.match(/\x1b\[[A-D]|[^\x1b]+/g) ?? []) {
      if (k === "\x1b[D") cc.at = Math.max(0, cc.at - 1);
      else if (k === "\x1b[C") cc.at = Math.min(cc.text.length, cc.at + 1);
      else if (k === "\x1b[A" || k === "\x1b[B") cc.history++;
    }
    setTimeout(() => (cc.draw ?? draw)(), cc.delay);                    // the echo comes a moment later, as from iTerm2
  };
  const page0 = await open(browser, { agent: true, screen: ["…"], hook, device: { viewport: { width: 1100, height: 700 } } });
  const { page, sent } = page0;
  say = page0.say;
  const set = async (text, pos) => { cc.text = text; cc.at = pos; cc.history = 0; draw(); await page.waitForTimeout(250); };
  draw();
  await page.waitForFunction(() => document.querySelector("#screen .blk.inbox .cur"), null, { timeout: 5000 });
  await page.evaluate(() => document.getElementById("kbd").focus());
  const where = () => page.evaluate(() => { const r = document.querySelector("#screen .cur").getBoundingClientRect(); return { x: Math.round(r.left), y: Math.round(r.top), h: Math.round(r.height) }; });
  const cell = await page.evaluate(() => {
    const t = document.getElementById("term"), p = document.createElement("span");
    p.textContent = "0".repeat(10); p.style.cssText = "position:absolute;visibility:hidden;white-space:pre"; t.append(p);
    const w = p.getBoundingClientRect().width / 10; p.remove(); return w;
  });
  const press = async (key, n = 1) => { for (let i = 0; i < n; i++) await page.keyboard.press(key); await page.waitForTimeout(250); };

  // the cursor on the page's second line, then ↑: the character drawn above it, not iTerm's row above
  await set(TEXT, 150);
  const before = await where();
  const sentFrom = sent.length;
  await press("ArrowUp");
  const up = await where(), upAt = cc.at;
  check(`${at} ↑ goes to the character drawn above the cursor`,
    up.y < before.y && before.y - up.y <= before.h + 2 && Math.abs(up.x - before.x) <= cell / 2 + 1 && cc.history === 0, JSON.stringify({ before, up, at: upAt, cell }));
  await press("ArrowDown");
  check(`${at} ↓ comes back to the same character`, cc.at === 150, JSON.stringify({ at: cc.at }));
  const keys = sent.slice(sentFrom).filter((m) => m.t === "in").map((m) => m.data);
  check(`${at} the moves go as ← and →, never as iTerm's ↑ and ↓`, keys.length === 2 && keys.every((k) => /^(\x1b\[[CD])+$/.test(k)), JSON.stringify(keys.map((k) => k.length)));

  // two quick presses with a slow echo (a phone, ssh): two lines, the second from the first's target
  cc.delay = 400;
  await page.keyboard.press("ArrowUp"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowUp");
  await page.waitForTimeout(700);
  cc.delay = 20;
  check(`${at} quick presses before the echo each move a line, from where the last one goes`, cc.at === upAt, JSON.stringify({ at: cc.at, upAt }));

  // on the input's first row ↑ is the agent's own key (history); on the page's first line but
  // iTerm's second row it goes to the start; a held ↑ stops there instead of running into history
  await set(TEXT, 40);
  await press("ArrowUp");
  check(`${at} ↑ on the input's first row is the agent's own key (its history)`, cc.history === 1 && cc.at === 40, JSON.stringify(cc));
  await set(TEXT, 70);
  await page.keyboard.press("ArrowUp");
  for (let i = 0; i < 8; i++) {
    await page.evaluate(() => document.getElementById("kbd").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", code: "ArrowUp", repeat: true, bubbles: true, cancelable: true })));
    await page.waitForTimeout(40);
  }
  await page.waitForTimeout(250);
  check(`${at} ↑ on the page's first line but iTerm's second row goes to the start; held, it stops there`, cc.at === 0 && cc.history === 0, JSON.stringify(cc));

  // paragraphs: ↑ from the second goes to the blank line between them, then into the first
  const PARAS = "first paragraph\n\nsecond paragraph here";
  await set(PARAS, 27);
  await press("ArrowUp");
  const blank = cc.at;
  await press("ArrowUp");
  const firstPara = cc.at;
  await press("ArrowDown", 2);
  check(`${at} ↑ and ↓ step over a blank line one line at a time, keeping their column`, blank === 16 && firstPara === 10 && cc.at === 27 && cc.history === 0,
    JSON.stringify({ blank, firstPara, back: cc.at }));

  // a line above out of sight is a line all the same
  await set(TEXT, 150);
  await page.evaluate(() => { const t = document.getElementById("term"), c = document.querySelector("#screen .cur"); t.scrollTop += c.getBoundingClientRect().top - t.getBoundingClientRect().top; });
  await press("ArrowUp");
  const hidden = cc.at;
  check(`${at} ↑ with the line above out of sight still goes one line up`, hidden === upAt, JSON.stringify({ hidden, upAt }));
  await page.evaluate(() => { const t = document.getElementById("term"); t.scrollTop = t.scrollHeight; });

  // a list the agent opened ("@") keeps its own ↑
  await set(TEXT + "\nlook at @", TEXT.length + 10);
  await press("ArrowUp");
  check(`${at} with a list open ("@") ↑ is the list's`, cc.history === 1, JSON.stringify(cc));

  // a click on a character of the input moves the cursor right there; right of the text after a typed space, nowhere
  await set(TEXT, TEXT.length);
  const target = await page.evaluate(() => {
    const box = document.querySelector("#screen .blk.inbox"), walk = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
    let t = walk.nextNode();
    while (t && !t.data.includes("input box")) t = walk.nextNode();
    const r = document.createRange(), i = t.data.indexOf("input box");
    r.setStart(t, i); r.setEnd(t, i + 1);
    const b = r.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  });
  await page.mouse.click(target.x, target.y); await page.waitForTimeout(250);
  check(`${at} a click on the input moves the cursor to the character clicked`, cc.text.slice(cc.at).startsWith("input box"), JSON.stringify({ at: cc.at, there: cc.text.slice(cc.at, cc.at + 15) }));
  await set("fix the ", 8);
  const after = await where();
  const tapFrom = sent.length;
  await page.mouse.click(after.x + 40, after.y + after.h / 2); await page.waitForTimeout(250);
  check(`${at} a click right of the text after a typed space leaves the cursor after the space`,
    cc.at === 8 && !sent.slice(tapFrom).some((m) => m.t === "in" && m.data), JSON.stringify({ at: cc.at }));

  // Claude Code's own rows while typing: each frame re-joins, and the cursor's place in the text is right
  const rec = JSON.parse(fs.readFileSync(new URL("./claude-input-60.json", import.meta.url), "utf8"));
  const wrong = [];
  for (const [k, [starts, [cr, cx]]] of rec.frames.entries()) {
    const typed = rec.text.slice(0, k + 1);
    const box = starts.map((st, i) => {
      const t = ((i ? "  " : rec.prompt) + typed.slice(st, starts[i + 1] ?? typed.length).replace(/ +$/, "")).padEnd(i === cr ? cx : 0);
      return i !== cr ? line(t) : { r: [[t.slice(0, cx), null, null, 0], [t[cx] ?? " ", null, null, 256], ...(t.slice(cx + 1) ? [[t.slice(cx + 1), null, null, 0]] : [])], e: true };
    });
    const rows = [line("⏺ Done."), line(""), line("─".repeat(rec.cols)), ...box, line("─".repeat(rec.cols)), line("  ~/app main")];
    say({ t: "screen", sid: "S1", full: true, n: rows.length, cols: rec.cols, rows: 30, ch: rows.map((r, i) => [i, r]) });
    await page.waitForTimeout(30);
    const got = await page.evaluate(async () => {
      const { layout } = await import("/boxnav.js");
      const rows = [...document.querySelectorAll("#screen .blk.inbox .ln:not(.rule)")];
      const lay = layout(rows.map((r) => ({ len: r.textContent.replace(/\ufe0e/g, "").trimEnd().length, glue: r.classList.contains("glue") })));
      const cur = document.querySelector("#screen .cur"), el = cur.closest(".ln");
      let col = 0;
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let t = w.nextNode(); t && !cur.contains(t); t = w.nextNode()) col += t.data.replace(/\ufe0e/g, "").length;
      return { joined: rows.slice(0, -1).every((r) => r.classList.contains("join")), at: lay[rows.indexOf(el)].start + Math.max(0, col - 2) };
    });
    if (!got.joined || got.at !== k + 1) wrong.push({ k, ...got });
  }
  check(`${at} Claude Code's recorded rows: all ${rec.frames.length} frames one line on the page, the cursor where it is in the text`,
    wrong.length === 0, JSON.stringify(wrong.slice(0, 3)));

  // Claude Code's own redraw (a status line's refresh) leaves the terminal's cursor below its footer:
  // the page keeps drawing it on the caret, and ↑ goes on from there
  await set(TEXT, 150);
  const caret = await where();
  cc.parked = true; draw(); await page.waitForTimeout(250);
  const parked = await page.evaluate(() => ({ n: document.querySelectorAll("#screen .cur").length, inBox: !!document.querySelector("#screen .blk.inbox .cur"),
    box: document.getElementById("term").classList.contains("hasbox") }));
  const shown = await where();
  check(`${at} the cursor Claude Code left below its footer is drawn on its caret, the box kept`,
    parked.n === 1 && parked.inBox && parked.box && shown.x === caret.x && shown.y === caret.y, JSON.stringify({ parked, caret, shown }));
  await press("ArrowUp");
  const back = await page.evaluate(() => document.querySelectorAll("#screen .cur").length);
  check(`${at} ↑ from there goes to the character drawn above, and one cursor is drawn after the echo`, cc.at === upAt && back === 1, JSON.stringify({ at: cc.at, upAt, back }));

  // the bridge sends only the rows that changed: after a new theme (rows drawn again) the caret
  // moves to another row and the row the page drew the cursor on is not sent again
  await set(TEXT, 150);
  cc.parked = true; draw(); await page.waitForTimeout(250);
  const theme = { bg: "#202020", fg: "#d0d0d0", cursor: "#ffffff", cursorText: "#000000", bold: "#ffffff", useBold: false,
    ansi: Array(16).fill("#888888"), font: null, size: null };
  say({ t: "theme", sid: "S1", theme }); await page.waitForTimeout(100);
  const was = screen(TEXT, 150, true), now = [...screen(TEXT, 20), line("")];
  const ch = now.map((r, i) => [i, r]).filter(([i, r]) => JSON.stringify(r) !== JSON.stringify(was[i]));
  say({ t: "screen", sid: "S1", full: false, n: now.length, cols: COLS, rows: 30, ch }); await page.waitForTimeout(250);
  const one = await page.evaluate(() => [...document.querySelectorAll("#screen .cur")].map((c) => c.closest(".ln").textContent.slice(0, 12)));
  check(`${at} a new theme while the cursor is parked, then a frame of the changed rows only: one cursor, on the caret's new row`,
    one.length === 1 && one[0].startsWith("❯") && ch.length === 2, JSON.stringify({ one, sent: ch.map(([i]) => i) }));
  cc.at = 20;

  // a long input (19 rows, as the owner's): its top rule 17 rows above the cursor is still the box's
  const TALL = Array.from({ length: 19 }, (_, i) => `line ${i + 1} of a long input`).join("\n");
  const tallAt = TALL.lastIndexOf("line 18") + 4;
  await set(TALL, tallAt);
  const tall = await where();
  const tallBox = await page.evaluate(() => !!document.querySelector("#screen .blk.inbox .cur") && document.getElementById("term").classList.contains("hasbox"));
  cc.parked = true; draw(); await page.waitForTimeout(250);
  const tallParked = await where(), inBox = await page.evaluate(() => document.querySelectorAll("#screen .blk.inbox .cur").length);
  check(`${at} a 19-row input is the box, and the cursor Claude Code left below its footer is drawn on its caret there`,
    tallBox && inBox === 1 && tallParked.x === tall.x && tallParked.y === tall.y, JSON.stringify({ tallBox, inBox, tall, tallParked }));
  cc.parked = false;

  // an input taller than the screen (50 rows on a 30-row screen; measured: Claude Code scrolls
  // its top rule and prompt up into the history): still the box, parked or not, and ↑ is the page's
  const HIGH = Array.from({ length: 50 }, (_, i) => `row ${i + 1} of a very long input`).join("\n");
  const split = (rows) => ({ hist: rows.slice(0, rows.length - 30), screen: rows.slice(-30) });
  const drawHigh = () => {
    const all = screen(cc.text, cc.at, cc.parked);
    if (!cc.parked) all.push(line(""));             // the screen's last row, where the cursor is left
    const { hist, screen: rows } = split(all);
    say({ t: "hist", mode: "reset", sid: "S1", first: 0, oldest: 0, truncated: false, lines: hist });
    say({ t: "screen", sid: "S1", full: true, n: rows.length, cols: COLS, rows: 30, ch: rows.map((r, i) => [i, r]) });
  };
  cc.text = HIGH; cc.at = HIGH.lastIndexOf("row 45") + 4; cc.history = 0; cc.draw = drawHigh; drawHigh(); await page.waitForTimeout(250);
  const high = await where();
  const highBox = await page.evaluate(() => document.querySelectorAll("#screen .blk.inbox .cur").length);
  cc.parked = true; drawHigh(); await page.waitForTimeout(250);
  const highParked = await where(), highIn = await page.evaluate(() => document.querySelectorAll("#screen .blk.inbox .cur").length);
  const highFrom = cc.at;
  await press("ArrowUp");
  check(`${at} a 50-row input taller than the screen (its top rule in the history) is the box: the cursor left below its footer is drawn on its caret, ↑ moves a line up`,
    highBox === 1 && highIn === 1 && highParked.x === high.x && highParked.y === high.y && cc.history === 0 && cc.at === highFrom - (HIGH.indexOf("row 45") - HIGH.indexOf("row 44")),
    JSON.stringify({ highBox, highIn, high, highParked, at: cc.at, highFrom, history: cc.history }));
  cc.parked = false; cc.draw = null;
  say({ t: "hist", mode: "reset", sid: "S1", first: 0, oldest: 0, truncated: false, lines: [] });

  // Claude Code's questions between rules, as drawn in iTerm2 (the hidden cursor at the chosen
  // item, 7 rows above the lower rule): ↓ is the menu's, not → to the next question
  const desc = line("     What this choice does, on a row of its own");
  cc.menu = [line("⏺ Done."), line(RULE), line("←  ☒ Old files  ☐ Keep  ✔ Submit  →"), line(""), line("Which files should go?"), line(""),
    line("  1. [✔] Drafts"), desc, desc, { r: [["❯", null, null, 256], [" 2. [✔] Branches", null, null, 0]], e: true }, desc, desc,
    line("  3. [ ] Logs"), desc, line("  4. [ ] Type something"), line("     Next"), line(RULE), line("  5. Chat about this"), line(""),
    line("Enter to select · Tab/Arrow keys to navigate · Esc to cancel")];
  cc.history = 0; draw(); await page.waitForTimeout(250);
  const menuFrom = sent.length;
  await press("ArrowDown");
  const menuKeys = sent.slice(menuFrom).filter((m) => m.t === "in").map((m) => m.data);
  const boxed = await page.evaluate(() => document.getElementById("term").classList.contains("hasbox"));
  check(`${at} in a menu between rules (Claude Code's questions) ↓ goes as it is, and it is not drawn as the input`,
    menuKeys.length === 1 && menuKeys[0] === "\x1b[B" && !boxed, JSON.stringify({ menuKeys, boxed }));
  await page.close();
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const pw = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
  const name = process.argv[2] ?? "chromium";
  const browser = await pw[name].launch();
  try { await webBoxNav(browser, name); } finally { await browser.close(); cleanup(); }
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
