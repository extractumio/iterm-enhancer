// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs on the whole web app (the real page files, app.js included)
// with the bridge played by a fake WebSocket: the profile bars, reminders of sessions waiting
// for an answer, focus, links with their underline and modifier key, long separators, View
// going back to the terminal, and files dropped on the page.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { check } from "./e2e_harness.mjs";

const STATIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bridge/fbbridge/web/static");
const ORIGIN = "http://web.test";
const TYPES = { html: "text/html", css: "text/css", svg: "image/svg+xml", js: "text/javascript" };

const item = (id, title, state, profile = "Default") =>
  ({ id, title, sub: ["claude", "~/p"], index: "", host: null, profile, agent: "claude", shell: false, state });
const layout = (asking, also = "done") => [{ kind: "window", label: "Window 1", where: "", host: null, wid: "W1", new: "New tab", pool: "",
  items: [item("S1", "Shown", "working"), item("S2", "Asking", asking), item("S3", "Also asking", also, "Work")] }];
const run = (text, flags = 0) => [text, null, null, flags];
const SCREEN = [
  { r: [run("See https://devbox.example/docs and src/mod.rs:42 or ~/notes.md")], e: true },
  { r: [run("─".repeat(80))], e: true },
  { r: [run("─".repeat(10))], e: true },
  { r: [run("❯ "), run(" ", 256)], e: true },
];
const THEME = { bg: "#1e1e1e", fg: "#d4d4d4", cursor: "#ffffff", cursorText: "#000000", bold: "#ffffff", useBold: false,
  ansi: Array.from({ length: 16 }, () => "#808080"), font: null, size: null, dark: true };

export async function webApp(browser) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
  const sent = [];
  let bridge = null;
  await page.addInitScript(() => { window.opened = []; window.open = (u) => { window.opened.push(u); return null; }; });
  await page.route(`${ORIGIN}/**`, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/auth/check") return route.fulfill({ status: 204, body: "" });
    if (p === "/upload") return route.fulfill({ contentType: "application/json", body: JSON.stringify({ path: "/tmp/up/a b.txt" }) });
    if (p === "/blank.html") return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>blank</title>" });
    const f = path.join(STATIC, p === "/" ? "index.html" : path.normalize(p));
    if (!f.startsWith(STATIC) || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    let body = fs.readFileSync(f);
    if (p === "/") body = body.toString().replace("__BUILD__", "test");
    return route.fulfill({ contentType: TYPES[f.split(".").pop()] ?? "application/octet-stream", body });
  });
  await page.routeWebSocket(`${ORIGIN.replace("http", "ws")}/ws`, (ws) => {
    bridge = ws;
    ws.onMessage((m) => {
      const msg = JSON.parse(String(m));
      sent.push(msg);
      if (msg.t === "sub") {
        ws.send(JSON.stringify({ t: "theme", sid: msg.id, theme: THEME }));
        ws.send(JSON.stringify({ t: "hist", mode: "reset", sid: msg.id, first: 0, oldest: 0, truncated: false, lines: [] }));
        ws.send(JSON.stringify({ t: "screen", sid: msg.id, full: true, n: SCREEN.length, cols: 80, rows: SCREEN.length, ch: SCREEN.map((l, i) => [i, l]) }));
      }
    });
    ws.send(JSON.stringify({ t: "build", id: "test" }));
    ws.send(JSON.stringify({ t: "layout", groups: layout("working") }));
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${ORIGIN}/`);
  await page.waitForFunction(() => document.querySelectorAll("#screen .ln").length === 4, null, { timeout: 10000 });
  const tell = (m) => bridge.send(JSON.stringify(m));
  const $eval = (fn, arg) => page.evaluate(fn, arg);

  // the profile bars: full for the shown session, breathing while its agent works
  const bars = await $eval(() => ({ on: [...document.querySelectorAll("#sessions .pbar.on")].map((b) => b.closest(".item").dataset.id),
    all: document.querySelectorAll("#sessions .pbar").length, header: !!document.querySelector("#curbar .pbar.on.busy"), dots: document.querySelectorAll("#sessions .pdot, #cursub .pdot").length }));
  check("AC-52 web app: a bar per session, full only for the shown one; the header's breathes while it works",
    bars.all === 3 && bars.on.join() === "S1" && bars.header && !bars.dots, JSON.stringify(bars));
  check("AC-52 web app: on a computer the chat takes the keys when it opens", await $eval(() => document.activeElement?.id === "kbd"));

  // reminders: the bridge sends a layout only when it changes; a session waiting 2.5 s with no
  // layout saying otherwise gets one; [x] and a click end it; it goes 2.5 s after waiting stops
  const cards = () => $eval(() => [...document.querySelectorAll("#asks .ask:not([hidden]) .what")].map((e) => e.textContent));
  const settle = () => page.waitForTimeout(2700);
  tell({ t: "layout", groups: layout("waiting") });
  await page.waitForTimeout(100);
  const soon = await cards();
  await settle();
  const later = await cards();
  check("AC-52 web app: a session that keeps waiting 2.5 s gets a reminder, not one that waited a moment",
    soon.length === 0 && later.join() === "Asking waits for your answer", JSON.stringify({ soon, later }));
  await page.click("#asks .ask .x");
  const closed = await cards();
  tell({ t: "layout", groups: layout("done") });
  await settle();
  tell({ t: "layout", groups: layout("waiting") });
  await settle();
  const back = await cards();
  check("AC-52 web app: a closed reminder stays closed, and comes back when that session asks again",
    closed.length === 0 && back.join() === "Asking waits for your answer", JSON.stringify({ closed, back }));
  tell({ t: "layout", groups: layout("waiting", "waiting") });
  await settle();
  const both = await cards();
  await page.click("#asks .ask .go");
  await page.waitForTimeout(100);
  const opened1 = sent.filter((m) => m.t === "sub").at(-1), left = await cards();
  check("AC-52 web app: a click on a reminder opens that session, and only its reminder goes",
    both.length === 2 && opened1?.id === "S2" && left.join() === "Also asking waits for your answer", JSON.stringify({ both, opened1, left }));
  tell({ t: "layout", groups: layout("waiting", "done") });
  await page.waitForTimeout(100);
  const stays = await cards();
  await settle();
  check("AC-52 web app: a reminder goes 2.5 s after its session stops waiting", stays.length === 1 && (await cards()).length === 0, JSON.stringify(stays));
  await page.click("#sessions .item[data-id='S1']");
  await page.waitForFunction(() => document.querySelectorAll("#screen .ln").length === 4);

  // links: a faint dotted underline; with a mouse they open with ⌘ (Ctrl elsewhere) only
  const marks = await $eval(() => new Promise((r) => setTimeout(() => r(CSS.highlights?.get("lnk")?.size ?? -1), 400)));
  check("AC-52 web app: the addresses and paths are underlined, without changing the lines", marks === 3, String(marks));
  const at = await $eval(() => {
    const t = document.querySelector("#screen .ln span").firstChild, r = new Range();
    r.setStart(t, t.data.indexOf("devbox")); r.setEnd(t, t.data.indexOf("devbox") + 6);
    const b = r.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  });
  await page.mouse.click(at.x, at.y);
  const plain = await $eval(() => window.opened.length);
  const mod = process.platform === "darwin" ? "Meta" : "Control";
  await page.keyboard.down(mod);
  await page.mouse.click(at.x, at.y);
  await page.keyboard.up(mod);
  const opened = await $eval(() => window.opened);
  check("AC-52 web app: a plain click on an address opens nothing; with the modifier key it opens", plain === 0 && opened.length === 1
    && opened[0].startsWith("https://devbox.example/docs"), JSON.stringify(opened));

  const rules = await $eval(() => [...document.querySelectorAll("#screen .ln.rule")].map((l) => [l.classList.contains("full"), Math.round(l.getBoundingClientRect().width)]));
  const termWidth = await $eval(() => document.getElementById("term").clientWidth);
  check("AC-52 web app: a separator half of iTerm's width or longer runs across the view; a short one stays text",
    rules[0][0] && !rules[1][0] && rules[0][1] >= termWidth - 20, JSON.stringify({ rules, termWidth }));

  // renaming hands the keys back to the chat
  await page.click("#rename");
  await page.fill("#renamename", "New name");
  await page.keyboard.press("Enter");
  check("AC-52 web app: after renaming, the chat has the keys again", await $eval(() => document.activeElement?.id === "kbd")
    && sent.some((m) => m.t === "rename" && m.name === "New name"));

  // View: closing its last file goes back to the terminal; the button below does too
  const openView = () => $eval(() => {
    const b = document.querySelector('button[data-view="file"]');
    b.disabled = false; b.click();
    document.getElementById("file").src = "/blank.html";
  });
  const emptied = async () => {
    await page.waitForFunction(() => document.getElementById("file").contentDocument?.title === "blank");
    await page.frame({ url: /blank\.html/ }).evaluate(() => parent.postMessage({ type: "fb-empty", path: "" }, location.origin));
    await page.waitForTimeout(100);
  };
  await openView();
  const wideOpen = await $eval(() => !document.getElementById("filewin").hidden);
  await emptied();
  check("AC-52 web app: closing View's last file closes its window over the terminal", wideOpen
    && await $eval(() => document.getElementById("filewin").hidden && document.querySelector('button[data-view="file"]').disabled));
  await page.setViewportSize({ width: 800, height: 800 });
  await openView();
  const narrowOpen = await $eval(() => document.body.dataset.shown);
  await emptied();
  const afterEmpty = await $eval(() => document.body.dataset.shown);
  await openView();
  await page.click("#backterm");
  check("AC-52 web app: below 1400 px closing View's last file, or its Terminal button, shows the terminal",
    narrowOpen === "file" && afterEmpty === "term" && await $eval(() => document.body.dataset.shown) === "term", JSON.stringify({ narrowOpen, afterEmpty }));

  // files dropped on the page: uploaded, their paths pasted; the page stays
  const before = sent.length;
  await $eval(() => {
    const dt = new DataTransfer();
    dt.items.add(new File(["hello"], "a b.txt", { type: "text/plain" }));
    document.getElementById("term").dispatchEvent(new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true }));
    document.getElementById("term").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  });
  await page.waitForTimeout(400);
  const typed = sent.slice(before).filter((m) => m.t === "in").map((m) => m.data).join("");
  check("AC-52 web app: a file dropped on the terminal is uploaded and its quoted path pasted, without Enter",
    typed.includes("'/tmp/up/a b.txt'") && !typed.includes("\r") && page.url() === `${ORIGIN}/`, JSON.stringify(typed));

  check("AC-52 web app: no page errors", !errors.length, errors.join(" | "));
  await page.close();
}
