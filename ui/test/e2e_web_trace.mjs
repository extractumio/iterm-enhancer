// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-55 checks for e2e_panel.mjs: the latency trace of the whole web app (the real page files)
// against a fake bridge WebSocket: keys numbered and timed to their echo, a session opened,
// pings, the badge, the end, and nothing of it without ?trace=1.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { check } from "./e2e_harness.mjs";

const STATIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bridge/fbbridge/web/static");
const ORIGIN = "http://web.test";
const TYPES = { html: "text/html", css: "text/css", svg: "image/svg+xml", js: "text/javascript" };
const item = (id, title) => ({ id, title, sub: ["zsh", "~/p"], index: "", host: null, profile: "Default", agent: null, shell: true, state: "" });
const LAYOUT = [{ kind: "window", label: "Window 1", where: "", host: null, wid: "W1", new: "New tab", pool: "", items: [item("S1", "One"), item("S2", "Two")] }];
const run = (text, flags = 0) => [text, null, null, flags];
const THEME = { bg: "#1e1e1e", fg: "#d4d4d4", cursor: "#ffffff", cursorText: "#000000", bold: "#ffffff", useBold: false,
  ansi: Array.from({ length: 16 }, () => "#808080"), font: null, size: null, dark: true };

/** The whole page against a fake bridge that echoes what is typed. */
export async function open(browser, query, device = { viewport: { width: 1400, height: 800 } }) {
  const page = await browser.newPage(device);
  const sent = [], errors = [];
  let typed = "";
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.route(`${ORIGIN}/**`, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/auth/check") return route.fulfill({ status: 204, body: "" });
    const f = path.join(STATIC, p === "/" ? "index.html" : path.normalize(p));
    if (!f.startsWith(STATIC) || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    let body = fs.readFileSync(f);
    if (p === "/") body = body.toString().replace("__BUILD__", "test");
    return route.fulfill({ contentType: TYPES[f.split(".").pop()] ?? "application/octet-stream", body });
  });
  await page.routeWebSocket(`${ORIGIN.replace("http", "ws")}/ws`, (ws) => {
    const say = (m) => ws.send(JSON.stringify(m));
    const screen = (sid, full, rows, extra = {}) => say({ t: "screen", sid, full, n: 3, cols: 80, rows: 3, ch: rows, ...extra });
    let sid = null;
    ws.onMessage((m) => {
      const msg = JSON.parse(String(m));
      sent.push(msg);
      if (msg.t === "sub") {
        sid = msg.id;
        say({ t: "theme", sid, theme: THEME });
        say({ t: "hist", mode: "reset", sid, first: 0, oldest: 0, truncated: false, lines: [] });
        screen(sid, true, [[0, { r: [run("hello")], e: true }], [1, { r: [run("$ "), run(" ", 256)], e: true }], [2, { r: [], e: true }]]);
      }
      if (msg.t === "trace" && "on" in msg) say({ t: "trace", on: msg.on, left: 900, file: "trace-20261009-120000.jsonl" });
      if (msg.t === "trace" && msg.ping != null) say({ t: "trace", pong: msg.ping });
      if (msg.t === "in") {
        typed += msg.data;
        const tr = msg.k ? { tr: { ks: [{ k: msg.k, fwd: 0.5, type: 1.5, via: "text", woke: "wake", wait: 4, polls: 0, read: 2, bt: 9, skip: 0 }], enc: 0.4 } } : {};
        setTimeout(() => screen(sid, false, [[1, { r: [run("$ " + typed), run(" ", 256)], e: true }]], tr), 30);
      }
    });
    say({ t: "build", id: "test" });
    say({ t: "layout", groups: LAYOUT });
  });
  await page.goto(`${ORIGIN}/${query}`);
  await page.waitForFunction(() => document.querySelectorAll("#screen .ln").length === 3, null, { timeout: 10000 });
  return { page, sent, errors };
}

const records = (sent) => sent.filter((m) => m.t === "trace" && Array.isArray(m.ev)).flatMap((m) => m.ev);

export async function webTrace(browser) {
  const { page, sent, errors } = await open(browser, "?trace=1");
  await page.waitForTimeout(200);
  check("AC-55 trace: ?trace=1 asks the bridge to record, and a badge says so",
    sent.some((m) => m.t === "trace" && m.on === true && m.resume === false) && /trace \d+:\d\d/.test(await page.textContent("#tracebadge")));

  await page.focus("#kbd");
  await page.keyboard.type("ab", { delay: 120 });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(400);
  const ins = sent.filter((m) => m.t === "in");
  check("AC-55 trace: each key carries its number", ins.length === 3 && ins.map((m) => m.k).join() === "1,2,3", JSON.stringify(ins));

  await page.click('#sessions .item[data-id="S2"]');
  await page.waitForTimeout(2300);                       // a batch every 2 s
  const recs = records(sent);
  const echo = recs.filter((r) => r.ev === "echo");
  const a = echo.find((r) => r.k === 1);
  check("AC-55 trace: a key's echo has the page's and the bridge's stages, its kind and path",
    echo.length === 3 && a && a.cls === "char" && ["keydown", "input"].includes(a.path) && a.total > 0 && a.rt >= 0 && a.draw >= 0 &&
      a.paint >= 0 && a.bt === 9 && a.via === "text" && a.enc === 0.4 && echo.find((r) => r.k === 3)?.cls === "enter", JSON.stringify(echo));
  check("AC-55 trace: nothing typed is recorded", !JSON.stringify(recs).includes('"data"') && !recs.some((r) => "t0" in r || "t1" in r), JSON.stringify(recs));
  const opened = recs.find((r) => r.ev === "open");
  check("AC-55 trace: a session opened is timed to its first screen and frame",
    opened?.sid === "S2" && opened.first >= 0 && opened.paint >= opened.first, JSON.stringify(opened));
  check("AC-55 trace: pings every 2 s, timed when the pong comes", sent.some((m) => m.t === "trace" && m.ping === 1) &&
    (await page.waitForTimeout(2100), records(sent).some((r) => r.ev === "ping" && r.rtt >= 0)));
  await page.click("#tracebadge");
  await page.waitForTimeout(200);
  check("AC-55 trace: a tap on the badge ends the trace", sent.some((m) => m.t === "trace" && m.on === false) &&
    /trace ended · trace-20261009-120000.jsonl/.test(await page.textContent("#tracebadge")));
  check("AC-55 trace: no page errors", !errors.length, errors.join(" | "));
  await page.close();

  const plain = await open(browser, "");
  await plain.page.focus("#kbd");
  await plain.page.keyboard.type("x");
  await plain.page.waitForTimeout(300);
  const msgs = plain.sent.filter((m) => m.t === "in" || m.t === "trace");
  check("AC-55 trace: without ?trace=1 no key number, no trace message, no badge",
    msgs.length === 1 && msgs[0].t === "in" && !("k" in msgs[0]) && !(await plain.page.$("#tracebadge")), JSON.stringify(msgs));
  await plain.page.close();
}
