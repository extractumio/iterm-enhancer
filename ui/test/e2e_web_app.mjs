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

  // a coding agent's input box as a panel; its footer behind a button on a phone
  const note = "✔ Update installed · Restart to update";
  const screenOf = (input, below) => [{ r: [run("output")], e: true }, { r: [run("     (ctrl+b to run in background)")], e: true },
    { r: [run(" ".repeat(80 - note.length) + note)], e: true }, { r: [run(" ".repeat(30) + "deep(nested, code)")], e: true },
    { r: [run("━".repeat(40) + " 4.2/4.2 MB 12.3 MB/s eta 0:00:00")], e: true },
    { r: [run([..."─".repeat(48) + " Compromised Node.js packages ─"])], e: true },   // cells, as the bridge sends box drawing
    { r: [run("❯ " + input), run(" ", 256)], e: true }, { r: [run("─".repeat(80))], e: true }, ...below.map((t) => ({ r: [run(t)], e: true }))];
  const STATUS = ["  ~/p Opus 5.5 ctx:55%", "  ⏵⏵ bypass permissions on"];
  const send = (rows) => tell({ t: "screen", sid: "S1", full: true, n: rows.length, cols: 80, rows: rows.length, ch: rows.map((l, i) => [i, l]) });
  send(screenOf("", STATUS));                                    // idle: its status lines are learnt
  await page.waitForTimeout(100);
  send(screenOf("hi", ["  ~/p Opus 5.5 ctx:61%", "  ⏵⏵ bypass permissions on"]));
  await page.waitForTimeout(150);
  const shown = (sel) => $eval((q) => [...document.querySelectorAll(q)].map((e) => getComputedStyle(e).display !== "none"), sel);
  const box = await $eval(() => ({ has: document.getElementById("term").classList.contains("hasbox"),
    inbox: [...document.querySelectorAll("#screen .blk.inbox .ln")].map((l) => l.textContent.trim().slice(0, 12)),
    foot: document.querySelectorAll("#screen .blk.foot .ln").length }));
  check("AC-52 web app: an agent's input box, with its rules, is one block and its status lines another",
    box.has && box.inbox.length === 3 && box.inbox[1] === "❯ hi" && box.foot === 2, JSON.stringify(box));
  const kept = await $eval(() => ({ bar: document.querySelectorAll(".ln.labelled").length, deep: [...document.querySelectorAll("#screen .ln")].find((l) => l.textContent.includes("deep(")).classList.contains("ralign") === false }));
  check("AC-52 web app: a progress bar is no titled rule, and a deeply indented line keeps its indent", kept.bar === 1 && kept.deep, JSON.stringify(kept));
  const phone = { foot: await shown("#screen .blk.foot .ln"), fab: await shown("#footfab"), hint: await shown(".ln.keyhint"),
    inbox: await $eval(() => [...document.querySelectorAll(".inbox .ln")].filter((l) => getComputedStyle(l).display !== "none").map((l) => l.textContent.trim())),
    lead: await shown(".ln.ralign .lead") };
  check("AC-52 web app: on a phone the box shows only its input, the footer waits behind a button, key hints and a notice's padding go",
    phone.inbox.join() === "❯ hi" && phone.foot.join() === "false,false" && phone.fab[0] && !phone.hint[0] && !phone.lead[0], JSON.stringify(phone));
  const tall = await $eval(() => { const b = document.querySelector(".blk.inbox"), lh = parseFloat(getComputedStyle(document.getElementById("term")).fontSize) * 1.25;
    return { h: b.clientHeight, min: 2 * lh }; });
  check("AC-52 web app: on a phone the input panel is at least two rows high", tall.h >= tall.min, JSON.stringify(tall));
  await page.click("#footfab");
  check("AC-52 web app: the button shows the footer", (await shown("#screen .blk.foot .ln")).join() === "true,true" && await $eval(() => document.getElementById("footfab").getAttribute("aria-pressed") === "true"));
  await page.click("#footfab");
  send(screenOf("/", ["  /help   Get help with using Claude Code", "  /model  Set the AI model", ...STATUS]));
  await page.waitForTimeout(150);
  const menu = await shown("#screen .blk.foot .ln");
  check("AC-52 web app: a \"/\" list below the input stays in sight; only the learnt status lines wait behind the button",
    menu.join() === "true,true,false,false", JSON.stringify(menu));
  await page.setViewportSize({ width: 1400, height: 800 });
  check("AC-52 web app: with a mouse on a wide screen the footer stays, with no button", !(await shown("#screen .blk.foot .ln")).includes(false) && !(await shown("#footfab"))[0]);

  // after a clear the prompt stands at the top: the empty rows below it do not fill the view
  const cleared = [{ r: [run("$ "), run(" ", 256)], e: true }, ...Array.from({ length: 30 }, () => ({ r: [], e: true })),
    { r: [["   ", null, 4, 0]], e: true }, ...Array.from({ length: 5 }, () => ({ r: [], e: true }))];   // a coloured row counts as drawn
  send(cleared);
  await page.waitForTimeout(150);
  const spare = await $eval(() => {
    const t = document.getElementById("term"); t.scrollTop = t.scrollHeight;
    const rows = [...document.querySelectorAll("#screen .ln")], cur = document.querySelector("#screen .cur").getBoundingClientRect(), v = t.getBoundingClientRect();
    return { shown: rows.filter((l) => getComputedStyle(l).display !== "none").length, inView: cur.top >= v.top && cur.bottom <= v.bottom };
  });
  check("AC-52 web app: empty rows below the screen's last drawing and the cursor are left out, so the prompt is in sight",
    spare.shown === 32 && spare.inView, JSON.stringify(spare));

  // a new terminal: an empty screen first, then the shell's prompt near its top
  const blank = () => ({ r: [], e: true });
  send([{ r: [run("Last login: today")], e: true }, { r: [run(" ", 256)], e: true }, ...Array.from({ length: 62 }, blank)]);
  await page.waitForTimeout(100);
  tell({ t: "screen", sid: "S1", full: false, n: 64, cols: 80, rows: 64, ch: [[1, blank()], [2, { r: [run("~ via v3.13")], e: true }], [3, { r: [run("at 15:51 ❯ "), run(" ", 256)], e: true }]] });
  await page.waitForTimeout(150);
  const fresh = await $eval(() => {
    const t = document.getElementById("term"), v = t.getBoundingClientRect(), cur = document.querySelector("#screen .cur").getBoundingClientRect();
    return { shown: [...document.querySelectorAll("#screen .ln")].filter((l) => getComputedStyle(l).display !== "none").length, inView: cur.top >= v.top && cur.bottom <= v.bottom };
  });
  check("AC-52 web app: a new terminal shows its prompt, not the empty rows below it, also when the prompt comes later",
    fresh.shown === 4 && fresh.inView, JSON.stringify(fresh));

  // a tab's other panes (a coding agent's subagents) go under its first one, as children
  const pane = (id, title, n) => ({ ...item(id, title, "working"), index: "3", pane: n });
  tell({ t: "layout", groups: [{ ...layout("done")[0], items: [item("S1", "Shown", "working"), pane("P1", "Orange notes", 1),
    pane("P2", "pragmatic", 2), pane("P3", "general-purpose", 3), item("S4", "Deploy", "done")] }] });
  await page.waitForTimeout(150);
  const tree = await $eval(() => ({
    top: [...document.querySelectorAll("#sessions .grp > ul > li > .item")].map((b) => b.dataset.id),
    kids: [...document.querySelectorAll('#sessions .item[data-id="P1"] ~ .kids .item.child')].map((b) => b.dataset.id),
    kidGrips: document.querySelectorAll("#sessions .kids .grip").length,
    kidIdx: [...document.querySelectorAll("#sessions .kids .idx")].map((i) => i.textContent).join(""),
    indent: document.querySelector("#sessions .kids .item").getBoundingClientRect().left - document.querySelector('#sessions .item[data-id="P1"]').getBoundingClientRect().left }));
  await page.fill("#filter", "pragmatic");
  const flat = await $eval(() => [...document.querySelectorAll("#sessions .item")].map((b) => b.dataset.id + (b.classList.contains("child") ? "*" : "")));
  await page.fill("#filter", "");
  check("AC-52 web app: a tab's other panes go under its first one, shifted in, with no tab number or grip; a filter shows them flat",
    tree.top.join() === "S1,P1,S4" && tree.kids.join() === "P2,P3" && !tree.kidGrips && !tree.kidIdx && tree.indent > 10 && flat.join() === "P2",
    JSON.stringify({ tree, flat }));

  // on a computer a page out of focus pauses its stream; the Files frame's focus is the page's
  const flow = async (focused, ev) => {
    const before = sent.length;
    await $eval(([f, e]) => { document.hasFocus = () => f; dispatchEvent(new Event(e)); }, [focused, ev]);
    await page.waitForTimeout(50);
    return sent.slice(before).filter((m) => m.t === "pause" || m.t === "resume").map((m) => m.t).join();
  };
  const away = await flow(false, "blur"), inFrame = await flow(true, "blur"), still = await flow(true, "focus");
  const again = await flow(false, "blur"), returned = await flow(true, "focus");
  check("AC-52 web app: on a computer a page out of focus pauses its stream and resumes in focus; focus in the Files frame does not pause",
    away === "pause" && inFrame === "resume" && still === "" && again === "pause" && returned === "resume", JSON.stringify({ away, inFrame, still, again, returned }));

  check("AC-52 web app: no page errors", !errors.length, errors.join(" | "));
  await page.close();
}
