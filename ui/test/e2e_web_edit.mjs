// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs: editing in a coding agent's input box from a computer (the page
// against a fake bridge): its rows re-join as Claude Code wrapped them (measured in iTerm2: rows of
// cols - 2 characters), a click moves the cursor there, a held delete goes by words after a while.
// Alone: node test/e2e_web_edit.mjs.

import { fileURLToPath } from "node:url";
import { check, cleanup, results } from "./e2e_harness.mjs";
import { open } from "./e2e_web_widgets.mjs";

const line = (text) => ({ r: [[text, null, null, 0]], e: true });
/** A row with the cursor at column x (padded past the text, as the bridge sends it). */
const cur = (t, x) => (x < t.length
  ? { r: [[t.slice(0, x), null, null, 0], [t[x], null, null, 256], [t.slice(x + 1), null, null, 0]].filter((r) => r[0] !== ""), e: true }
  : { r: [...(t ? [[t, null, null, 0]] : []), ...(x > t.length ? [[" ".repeat(x - t.length), null, null, 0]] : []), [" ", null, null, 256]], e: true });
const RULE = "─".repeat(80);
const FULL = "❯ " + Array(7).fill("abcdefghi").join(" ") + " abcdef";       // 78 characters: a full row at 80 columns

export async function webEdit(browser) {
  const at = "AC-52 web app, editing in a coding agent's input box:";
  const { page, sent, say } = await open(browser, { agent: true, screen: ["…"] });
  const show = async (box) => {
    const rows = [line("⏺ Done."), line(""), line(RULE), ...box, line(RULE), line("  ~/app main"), line("  ⏵⏵ accept edits on")];
    say({ t: "screen", sid: "S1", full: true, n: rows.length, cols: 80, rows: 30, ch: rows.map((r, i) => [i, r]) });
    await page.waitForFunction(() => document.querySelector("#screen .blk.inbox .cur"), null, { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(150);
  };
  // where a box row's last character and the cursor are drawn
  const geo = () => page.evaluate(() => {
    const rows = [...document.querySelectorAll("#screen .blk.inbox .ln:not(.rule)")];
    const end = (el) => {
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let last = null;
      for (let t = w.nextNode(); t; t = w.nextNode()) if (t.data.trim() && !t.parentElement.closest(".cur")) last = t;
      if (!last) return null;
      const r = document.createRange(), i = last.data.trimEnd().length;
      r.setStart(last, i - 1); r.setEnd(last, i);
      const b = r.getBoundingClientRect();
      return { top: Math.round(b.top), right: Math.round(b.right) };
    };
    const c = document.querySelector("#screen .cur").getBoundingClientRect();
    return { ends: rows.map(end), cur: { top: Math.round(c.top), left: Math.round(c.left) } };
  });

  // a space typed at a full row's end: Claude Code puts the cursor alone on the next row
  await show([line(FULL), cur("", 2)]);
  const space = await geo();
  check(`${at} a space typed at a full row's end keeps the cursor on that line, after the text (no jump to a new line)`,
    space.cur.top === space.ends[0].top && space.cur.left > space.ends[0].right, JSON.stringify(space));

  // a line the user ended (Shift+Enter) after a long row that was not full stays two lines
  await show([line("❯ " + "w".repeat(70)), cur("  abcde", 7)]);
  const ended = await geo();
  check(`${at} a line ended by the user after a long row stays its own line (Claude Code's row holds cols - 2)`,
    ended.ends[1] && ended.ends[1].top > ended.ends[0].top, JSON.stringify(ended));
  await show([line(FULL.slice(0, 71)), cur("  abcdefg", 9)]);
  const wrapped = await geo();
  check(`${at} a word Claude Code moved to the next row goes on the same line`, wrapped.ends[1]?.top === wrapped.ends[0].top, JSON.stringify(wrapped));

  // a click on the input moves the cursor there with arrow keys; ⌘-click does not
  await show([line("❯ first line of the text"), cur("  second line", 13)]);
  const target = await page.evaluate(() => {
    const row = [...document.querySelectorAll("#screen .blk.inbox .ln:not(.rule)")][0];
    const t = document.createTreeWalker(row, NodeFilter.SHOW_TEXT).nextNode();
    const r = document.createRange(), i = t.data.indexOf("line");
    r.setStart(t, i); r.setEnd(t, i + 1);
    const b = r.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  });
  const clicked = sent.length;
  await page.mouse.click(target.x, target.y);
  await page.waitForTimeout(150);
  const moves = sent.slice(clicked).filter((m) => m.t === "in").map((m) => m.data);
  // along the input's text (boxnav.js): from the end of "second line" (offset 23 + 11) back to "line" (6)
  check(`${at} a click on the input moves the cursor there, along its text with ←`,
    JSON.stringify(moves) === JSON.stringify(["\x1b[D".repeat(28)]), JSON.stringify(moves.map((m) => m.length)));
  const meta = sent.length;
  await page.keyboard.down(process.platform === "darwin" ? "Meta" : "Control");
  await page.mouse.click(target.x, target.y);
  await page.keyboard.up(process.platform === "darwin" ? "Meta" : "Control");
  await page.waitForTimeout(150);
  check(`${at} ⌘-click (a link's click) moves nothing`, !sent.slice(meta).some((m) => m.t === "in"));

  // selecting text still selects it: a drag over the input and a double click on the output move nothing
  const drag = sent.length;
  await page.mouse.move(target.x - 40, target.y);
  await page.mouse.down();
  await page.mouse.move(target.x + 40, target.y, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  const dragged = await page.evaluate(() => String(getSelection()));
  const done = await page.evaluate(() => {
    const t = [...document.querySelectorAll("#screen .ln")].find((el) => el.textContent.includes("Done."));
    const r = t.getBoundingClientRect();
    return { x: r.left + 30, y: r.top + r.height / 2 };
  });
  await page.mouse.dblclick(done.x, done.y);
  await page.waitForTimeout(150);
  const word = await page.evaluate(() => String(getSelection()));
  check(`${at} a drag selects text and a double click a word, moving no cursor`,
    dragged.length > 3 && word.trim() === "Done" && !sent.slice(drag).some((m) => m.t === "in"), JSON.stringify({ dragged, word }));
  await page.evaluate(() => getSelection().removeAllRanges());

  // a held delete: characters first, then words (Ctrl+W), at most one each WORD_GAP
  const held = sent.length;
  await page.evaluate(async () => {
    const k = document.getElementById("kbd");
    k.focus();
    const key = (type, repeat = false) => k.dispatchEvent(new KeyboardEvent(type, { key: "Backspace", code: "Backspace", repeat, bubbles: true, cancelable: true }));
    key("keydown");
    for (const t0 = performance.now(); performance.now() - t0 < 2200;) { await new Promise((r) => setTimeout(r, 30)); key("keydown", true); }
    key("keyup");
  });
  await page.waitForTimeout(100);
  const deletes = sent.slice(held).filter((m) => m.t === "in").map((m) => m.data);
  const firstWord = deletes.indexOf("\x17"), words = deletes.filter((d) => d === "\x17").length;
  check(`${at} a held delete deletes characters, then after 1.5 s whole words, a few a second`,
    deletes[0] === "\x7f" && firstWord >= 30 && deletes.slice(0, firstWord).every((d) => d === "\x7f")
      && words >= 3 && words <= 9 && deletes.every((d) => d === "\x7f" || d === "\x17"), JSON.stringify({ n: deletes.length, firstWord, words }));
  await page.close();

  // at a shell's prompt words go too; after an Enter not, until the session list says the shell is back (nano: ^W searches)
  const shell = await open(browser, {});
  const hold = async () => {
    const from = shell.sent.length;
    await shell.page.evaluate(async () => {
      const k = document.getElementById("kbd");
      k.focus();
      const key = (repeat) => k.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace", code: "Backspace", repeat, bubbles: true, cancelable: true }));
      key(false);
      for (const t0 = performance.now(); performance.now() - t0 < 1800;) { await new Promise((r) => setTimeout(r, 30)); key(true); }
      k.dispatchEvent(new KeyboardEvent("keyup", { key: "Backspace", code: "Backspace", bubbles: true }));
    });
    await shell.page.waitForTimeout(400);                      // the next hold is a new press
    return shell.sent.slice(from).filter((m) => m.t === "in" && m.data === "\x17").length;
  };
  const atPrompt = await hold();
  await shell.page.keyboard.press("Enter");
  const afterEnter = await hold();
  shell.say({ t: "layout", groups: [{ kind: "window", label: "Window 1", where: "", host: null, wid: "W1", new: "New tab", pool: "",
    items: [{ id: "S1", title: "One", sub: ["zsh", "~/p"], index: "", host: null, profile: "Default", agent: null, shell: true, state: "" }] }] });
  await shell.page.waitForTimeout(100);
  const back = await hold();
  check(`${at} at a shell's prompt a held delete goes by words, not after an Enter until the list shows the shell again`,
    atPrompt > 0 && afterEnter === 0 && back > 0, JSON.stringify({ atPrompt, afterEnter, back }));
  await shell.page.close();
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const pw = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
  const browser = await pw.chromium.launch();
  try { await webEdit(browser); } finally { await browser.close(); cleanup(); }
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
