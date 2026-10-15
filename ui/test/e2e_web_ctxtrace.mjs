// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-55 checks for e2e_panel.mjs: the context trace of ?trace=2 on the whole web app against a
// fake bridge: its button left of ⓘ copies the page's last events (keys and their echo, a
// session opened, clicks, keys in other fields without what they write) and its state (sizes,
// the screen's rows, a coding agent's input box as iTerm2 has it and as the page lays it out),
// in the format of docs/web-trace.md, within its limits; nothing of it without ?trace=2.
// Alone: node test/e2e_web_ctxtrace.mjs.

import { fileURLToPath } from "node:url";
import { check, cleanup, results } from "./e2e_harness.mjs";
import { open as openShell } from "./e2e_web_trace.mjs";
import { open as openAgent } from "./e2e_web_widgets.mjs";
import { parseTrace, screenMessage } from "./tracereplay.mjs";

const COLS = 60;
const line = (text) => ({ r: [[text, null, null, 0]], e: true });
const RULE = "─".repeat(COLS);
// Claude Code's input of two rows at 60 columns, the cursor at its end
const BOX = ["❯ the problem with wrapping stays when the width of the", "  original iTerm2 window differs from the browser's"];
const AGENT = [line("⏺ Done."), line(""), line(RULE), line(BOX[0]),
  { r: [[BOX[1], null, null, 0], [" ", null, null, 256]], e: true }, line(RULE), line("  ~/app main")];

// what the page hands to the clipboard (an http page copies by execCommand, touchcopy.js)
const CATCH = () => {
  const ex = document.execCommand.bind(document);
  document.execCommand = (c, ...a) => { if (c === "copy") window.copied = String(getSelection()); return ex(c, ...a); };
};
async function copyTrace(page) {
  await page.evaluate(() => { window.copied = null; });
  await page.click("#tracefab");
  await page.waitForFunction(() => window.copied !== null, null, { timeout: 5000 });
  return page.evaluate(() => window.copied);
}
const LINE = /^(\d+) ([a-z]+) (\{.*\})$/;
/** Every line is a header, an event or a snapshot's named line, or a row. */
function badLines(text) {
  return text.trimEnd().split("\n").filter((l) => {
    if (/^(#ite-trace 1 \{.*\}|#state)$/.test(l) || /^\|-?\d+ [a-z-]+( [\d+*,]+)?\|/.test(l) || /^:\d+ -?[\d.]+\|/.test(l)) return false;
    const m = LINE.exec(l);
    try { return !m || typeof JSON.parse(m[3]) !== "object"; } catch { return true; }
  });
}
const events = (text, ev) => text.split("\n").map((l) => LINE.exec(l)).filter((m) => m && m[2] === ev).map((m) => JSON.parse(m[3]));

export async function webCtxTrace(browser, name = "chromium") {
  const at = `AC-55 context trace (${name}):`;
  const { page, sent, errors } = await openShell(browser, "?trace=2");
  await page.evaluate(CATCH);
  const order = await page.evaluate(() => [...document.getElementById("fabs").children].map((b) => b.id));
  const shown = await page.evaluate(() => { const b = document.getElementById("tracefab"); return !!b?.getClientRects().length && getComputedStyle(b).display !== "none"; });
  check(`${at} its button stands left of ⓘ and shows on a computer, no badge and no Mac log asked for`,
    order.indexOf("tracefab") >= 0 && order.indexOf("tracefab") === order.indexOf("footfab") - 1 && shown && !(await page.$("#tracebadge")) &&
      !sent.some((m) => m.t === "trace" && "on" in m) && sent.some((m) => m.t === "trace" && m.stages === true), JSON.stringify(order));

  await page.focus("#kbd");
  await page.keyboard.type("ab", { delay: 80 });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(300);
  await page.click("#filter");
  await page.keyboard.type("zq");
  await page.keyboard.press("Escape");
  await page.click('#sessions .item[data-id="S2"]');
  await page.waitForTimeout(400);
  const text = await copyTrace(page);
  const lines = text.trimEnd().split("\n");
  check(`${at} the copy is lines of its format, at most 1000 of at most 4000 characters`,
    lines[0].startsWith("#ite-trace 1 {") && lines.length <= 1000 && lines.every((l) => l.length <= 4000) && !badLines(text).length,
    badLines(text).slice(0, 5).join(" ⏎ "));
  const keys = events(text, "key"), echo = events(text, "echo");
  const ins = sent.filter((m) => m.t === "in");
  check(`${at} keys as sent with their number, each echo timed from the send to the screen and the paint, with the bridge's stages`,
    keys.map((k) => k.d).join("") === "ab\r" && keys.every((k) => ["keydown", "input"].includes(k.p) && k.in >= 0) && ins.map((m) => m.k).join() === "1,2,3" &&
      echo.length === 3 && echo.every((e) => e.rt >= 0 && e.paint >= 0 && e.total >= e.rt && e.br?.bt === 9 && e.br.via === "text" && e.net === Math.round((e.rt - 9) * 10) / 10) &&
      echo.map((e) => e.k).join() === "1,2,3", JSON.stringify({ keys, echo }));
  const kd = events(text, "kd");
  check(`${at} keys in another field are named, never what they write`,
    kd.some((k) => k.on === "#filter" && k.k === "·") && kd.some((k) => k.on === "#filter" && k.k === "Escape") &&
      !kd.some((k) => k.k === "z" || k.k === "q") && !/zq/.test(text), JSON.stringify(kd.filter((k) => k.on)));
  const opened = events(text, "open").find((o) => o.sid === "S2");
  check(`${at} a session opened is timed to its history, first screen, frame and input`,
    events(text, "pick").some((p) => p.sid === "S2") && opened && opened.hist >= 0 && opened.drawn >= opened.hist && opened.scr >= opened.hist &&
      opened.paint >= opened.scr && opened.ready >= opened.paint && opened.ready >= opened.drawn, JSON.stringify(opened));
  check(`${at} clicks and taps with their place and target`,
    events(text, "ptr").some((p) => p.k === "tap" && p.pt === "mouse" && /@#sessions/.test(p.on) && p.x > 0) &&
      events(text, "click").some((c) => /@#sessions/.test(c.on)), JSON.stringify(events(text, "ptr")));
  const size = events(text, "size")[0], scr = text.split("\n#state\n")[1] ?? "";
  check(`${at} the state: sizes, places, the session, the screen's rows with the cursor's run`,
    size?.win?.[0] === 1400 && size.iterm?.[0] === 80 && events(text, "places")[0]?.term && events(text, "sess")[0]?.sid === "S2" &&
      /\n\|0 e\|hello\n\|1 e 2\+1\*256\|\$\n/.test(scr), scr.slice(0, 600));
  check(`${at} no page errors`, !errors.length, errors.join(" | "));
  // an uncaught error with a long message and stack: one line, still JSON, the page's address left out
  await page.evaluate(() => setTimeout(() => { const e = new Error(`boom ${"x".repeat(300)}`); throw e; }));
  await page.waitForTimeout(100);
  errors.length = 0;
  const withErr = await copyTrace(page), err = events(withErr, "err")[0];
  check(`${at} an uncaught error is one line of JSON within 400 characters, without the page's address`,
    err && /boom x+/.test(err.msg) && !badLines(withErr).length && !/web\.test/.test(JSON.stringify(err)) &&
      withErr.split("\n").filter((l) => / err /.test(l)).every((l) => l.length <= 400), JSON.stringify(err));
  // the ring keeps its last events: the oldest go, and the copy says how many
  await page.evaluate(() => { for (let i = 0; i < 1500; i++) document.dispatchEvent(new KeyboardEvent("keydown", { key: "F5", bubbles: true })); });
  const full = await copyTrace(page), head = JSON.parse(full.split("\n")[0].slice(13));
  check(`${at} a full ring drops its oldest events and says how many`,
    head.dropped > 800 && head.events <= 640 && full.trimEnd().split("\n").length <= 1000 && !events(full, "key").length, JSON.stringify(head));
  await page.close();

  // a coding agent's input box on a phone: iTerm2's two rows, the page's lines of them
  const phone = { viewport: { width: 390, height: 760 }, hasTouch: true, isMobile: true };
  const ag = await openAgent(browser, { agent: true, device: phone, query: "?trace=2" });
  await ag.page.evaluate(CATCH);
  ag.say({ t: "screen", sid: "S1", full: true, n: AGENT.length, cols: COLS, rows: 30, ch: AGENT.map((r, i) => [i, r]) });
  await ag.page.waitForSelector("#screen .blk.inbox");
  await ag.page.waitForTimeout(500);
  const t2 = await copyTrace(ag.page);
  const st = t2.split("\n#state\n")[1] ?? "", boxAt = st.indexOf(" box {"), part = st.slice(boxAt).split("\n");
  const orig = part.slice(1, 5), webHead = part[5] ?? "", web = part.slice(6).filter((l) => l.startsWith(":"));
  const joined = (rows) => rows.map((l) => l.replace(/^[|:][^|]*\|/, "").trim()).join(" ").replace(/^❯ /, "").replace(/\s+/g, " ");
  check(`${at} an agent's input box: iTerm2's rows with their classes, the page's lines of it, the same text`,
    boxAt > 0 && /^\|2 e?rh?\|─/.test(orig[0]) && /^\|3 ej\|❯ the problem/.test(orig[1]) && /^\|4 ec? 51\+1\*256\|  original/.test(orig[2]) &&
      /^\d+ web \{/.test(webHead) && web.length > 2 && joined(web) === joined(orig.slice(1, 3)) &&
      JSON.stringify(JSON.parse(webHead.replace(/^\d+ web /, "")).cur) === "[2,18]",
    part.slice(0, 14).join(" ⏎ "));
  check(`${at} an input box that changed is kept with its time among the events`, /^\d+ box \{/m.test(t2.split("\n#state\n")[0]), "");
  // played again: the state's screen as the bridge sends it (tracereplay.mjs) is the screen that was sent
  const rowsOf = (ch) => ch.map(([i, d]) => [i, d.e, d.r.map(([t]) => t).join("").trimEnd(), d.r.findIndex(([, , , f]) => f & 256)]);
  const again = screenMessage(parseTrace(t2));
  check(`${at} its screen played again is the screen the bridge sent`,
    again.cols === COLS && JSON.stringify(rowsOf(again.ch)) === JSON.stringify(rowsOf(AGENT.map((r, i) => [i, r]))), JSON.stringify(rowsOf(again.ch)));
  check(`${at} the button on a phone, left of ⓘ`, await ag.page.evaluate(() => {
    const b = document.getElementById("tracefab").getBoundingClientRect(), i = document.getElementById("footfab").getBoundingClientRect();
    return b.width > 0 && i.width > 0 && b.right <= i.left;
  }));
  check(`${at} no page errors on the phone`, !ag.errors.length, ag.errors.join(" | "));
  await ag.page.close();

  const plain = await openShell(browser, "");
  check(`${at} without ?trace=2 no button and no ping`, !(await plain.page.$("#tracefab")) && (await plain.page.waitForTimeout(300), !plain.sent.some((m) => m.t === "trace")));
  await plain.page.close();
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const pw = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
  const name = process.argv[2] ?? "chromium";
  const browser = await pw[name].launch();
  try { await webCtxTrace(browser, name); } finally { await browser.close(); cleanup(); }
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
