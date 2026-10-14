// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs: typing on a touch screen (the page against a fake bridge, a
// phone's size): a held delete has something to delete (iOS repeats it only then), the keyboard
// field lies over a coding agent's whole input box (a long press there gets iOS's Paste), and a
// finger that rests and then slides moves the cursor. Alone: node test/e2e_web_touch.mjs.

import { fileURLToPath } from "node:url";
import { check, cleanup, results } from "./e2e_harness.mjs";
import { open } from "./e2e_web_widgets.mjs";

const line = (text, extra = {}) => ({ r: [[text, null, null, 0]], e: true, ...extra });
const RULE = "─".repeat(60);

export async function webTouch(browser) {
  const at = "AC-52 web app on a touch screen:";
  const { page, sent, say } = await open(browser, { agent: true, history: [line("$ claude"), ...Array.from({ length: 80 }, (_, i) => line(`⏺ step ${i} of the agent's work`))], screen: ["…"],
    device: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } });
  // a coding agent's screen: its answer, then its input box between two rules, the cursor in it
  const rows = [line("⏺ Done."), line(""), line(RULE), { r: [["> fix the ", null, null, 0], [" ", null, null, 256]], e: true }, line(RULE), line("  ? for shortcuts")];
  say({ t: "screen", sid: "S1", full: true, n: rows.length, cols: 60, rows: rows.length, ch: rows.map((r, i) => [i, r]) });
  await page.waitForFunction(() => document.querySelector("#screen .blk.inbox"), null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(200);
  const cover = await page.evaluate(() => {
    const k = document.getElementById("kbd").getBoundingClientRect(), b = document.querySelector("#screen .blk.inbox").getBoundingClientRect();
    return { k: [Math.round(k.top), Math.round(k.bottom)], box: [Math.round(b.top), Math.round(b.bottom)] };
  });
  check(`${at} the keyboard field lies over the coding agent's whole input box (a long press there is iOS's Paste)`,
    cover.k[0] === cover.box[0] && cover.k[1] === cover.box[1], JSON.stringify(cover));

  const shape = await page.evaluate(() => { const cs = getComputedStyle(document.getElementById("kbdbtn")); return [cs.borderTopLeftRadius, cs.borderTopRightRadius]; });
  check(`${at} the floating keyboard buttons are nearly round blobs, not circles`, shape[0] !== shape[1] && !shape.includes("50%"), JSON.stringify(shape));
  // every other button a little uneven: bowed edges (neighbors differ), a border as a ring of that shape, a round one a blob
  const uneven = await page.evaluate(() => {
    const cs = (el, p) => getComputedStyle(el, p), tabs = [...document.querySelectorAll("#views button")];
    const key = document.querySelector("#hotkeys .key"), down = cs(document.getElementById("bottom"));
    return { tabs: tabs.map((b) => cs(b).clipPath.startsWith("shape(")), differ: cs(tabs[0]).clipPath !== cs(tabs[1]).clipPath,
      save: cs(document.querySelector("#markbox .primary")).clipPath.startsWith("shape("),
      key: [cs(key).borderTopWidth, cs(key, "::after").backgroundColor, cs(key, "::after").clipPath.startsWith("shape(evenodd")],
      down: [down.borderTopLeftRadius, down.borderTopRightRadius, down.clipPath] };
  });
  check(`${at} buttons are a little uneven: the tabs' and the bookmark dialog's bowed (neighbors differently), a hot key's border a ring of its shape, Scroll to the bottom a blob`,
    uneven.tabs.every(Boolean) && uneven.differ && uneven.save && uneven.key[0] === "0px" && !/^rgba\(0, 0, 0, 0\)$|transparent/.test(uneven.key[1]) && uneven.key[2]
    && uneven.down[0] !== uneven.down[1] && !uneven.down.includes("50%") && uneven.down[2] === "none", JSON.stringify(uneven));
  await page.evaluate(() => document.getElementById("kbd").focus());
  const kept = await page.evaluate(() => document.getElementById("kbd").value);
  const before = sent.length;
  for (let i = 0; i < 3; i++) await page.keyboard.press("Backspace");
  await page.keyboard.type("ab");
  await page.waitForTimeout(100);
  const typed = sent.slice(before).filter((m) => m.t === "in").map((m) => m.data);
  check(`${at} the field keeps a character to delete, each delete is one backspace, typing is as before`,
    kept === "\u200b" && JSON.stringify(typed) === JSON.stringify(["\x7f", "\x7f", "\x7f", "a", "b"])
    && await page.evaluate(() => document.getElementById("kbd").value) === "\u200b", JSON.stringify({ kept, typed }));

  // the caret before the kept character (iOS's space-bar trackpad): a delete still goes; a cut sends nothing
  const edge = sent.length;
  await page.evaluate(() => document.getElementById("kbd").setSelectionRange(0, 0));
  await page.keyboard.press("Backspace");
  await page.evaluate(() => {
    const k = document.getElementById("kbd");
    k.dispatchEvent(new InputEvent("input", { inputType: "deleteByCut", bubbles: true }));
    k.dispatchEvent(new InputEvent("input", { inputType: "deleteCompositionText", bubbles: true }));
  });
  await page.waitForTimeout(100);
  const edged = sent.slice(edge).filter((m) => m.t === "in").map((m) => m.data);
  check(`${at} a delete with the caret before the kept character still goes; a cut or an IME's own delete sends nothing`,
    JSON.stringify(edged) === JSON.stringify(["\x7f"]), JSON.stringify(edged));

  // rest a finger on the input box, then slide right 3 characters and back 1
  const cdp = await page.context().newCDPSession(page);
  const geo = await page.evaluate(() => {
    const k = document.getElementById("kbd").getBoundingClientRect(), t = document.getElementById("term");
    const probe = document.createElement("span"); probe.textContent = "0".repeat(10); probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre"; t.append(probe);
    const w = probe.getBoundingClientRect().width / 10; probe.remove();
    return { x: k.left + 60, y: k.top + k.height / 2, w };
  });
  const touch = (type, x) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y: geo.y }] });
  const from = sent.length;
  await touch("touchStart", geo.x);
  await page.waitForTimeout(350);
  for (let k = 1; k <= 7; k++) await touch("touchMove", geo.x + (geo.w * 3.5 * k) / 7);
  await touch("touchMove", geo.x + geo.w * 2.5);
  await touch("touchEnd");
  await page.waitForTimeout(100);
  const moves = sent.slice(from).filter((m) => m.t === "in").map((m) => m.data);
  check(`${at} a finger that rests and then slides moves the cursor a character a step, both ways, and taps nothing`,
    JSON.stringify(moves) === JSON.stringify(["\x1b[C", "\x1b[C", "\x1b[C", "\x1b[D"]), JSON.stringify(moves));

  // a quick swipe is not a slide
  const swipeFrom = sent.length;
  await touch("touchStart", geo.x);
  for (let k = 1; k <= 5; k++) await touch("touchMove", geo.x + geo.w * k);
  await touch("touchEnd");
  await page.waitForTimeout(100);
  check(`${at} a quick swipe moves nothing`, !sent.slice(swipeFrom).some((m) => m.t === "in" && /\x1b\[[CD]/.test(m.data)));

  // a swipe up or down that starts on the input box scrolls the terminal
  await page.evaluate(() => { const t = document.getElementById("term"); t.scrollTop = t.scrollHeight; });
  const top0 = await page.evaluate(() => document.getElementById("term").scrollTop);
  const vertical = (type, y) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x: geo.x, y }] });
  await vertical("touchStart", geo.y);
  for (let k = 1; k <= 6; k++) await vertical("touchMove", geo.y + k * 20);
  await vertical("touchEnd");
  await page.waitForTimeout(100);
  const top1 = await page.evaluate(() => document.getElementById("term").scrollTop);
  check(`${at} a swipe down that starts on the input box scrolls the terminal`, top1 < top0, JSON.stringify({ top0, top1 }));
  await page.close();
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const pw = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
  const browser = await pw.chromium.launch();
  try { await webTouch(browser); } finally { await browser.close(); cleanup(); }
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
