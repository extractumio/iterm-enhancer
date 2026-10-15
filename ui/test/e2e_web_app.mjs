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
    if (p === "/fb/api/ls") {                    // the pane's files: src/mod.rs is there, ~/notes.md is not
      const q = new URL(route.request().url()).searchParams;
      const there = { "/Users/alex/p/src": ["mod.rs"] }[q.get("path")] ?? [];
      const entries = there.filter((n) => !q.get("filter") || n.includes(q.get("filter"))).map((n) => ({ n, k: "f" }));
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "ready", total: entries.length, writable: true, gen: 1, entries }) });
    }
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
      if (msg.t === "files") ws.send(JSON.stringify({ t: "files", sid: "S1", key: "S1", cwd: "/Users/alex/p", home: "/Users/alex", host: null }));
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
  await page.waitForFunction(() => CSS.highlights?.get("lnk")?.size === 2, null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  const marks = await $eval(() => [...(CSS.highlights?.get("lnk") ?? [])].map((r) => {
    const x = new Range(); x.setStart(r.startContainer, r.startOffset); x.setEnd(r.endContainer, r.endOffset); return x.toString();
  }));
  check("AC-52 web app: the addresses and the paths of files that are there are underlined, without changing the lines; a path not there is not",
    JSON.stringify(marks) === JSON.stringify(["https://devbox.example/docs", "src/mod.rs:42"]), JSON.stringify(marks));
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
  const notes = await $eval(() => {
    const t = document.querySelector("#screen .ln span").firstChild, r = new Range(), i = t.data.indexOf("notes.md");
    r.setStart(t, i); r.setEnd(t, i + 3);
    const b = r.getBoundingClientRect();
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  });
  const fileSrc = await $eval(() => document.getElementById("file").getAttribute("src"));
  await page.keyboard.down(mod);
  await page.mouse.click(notes.x, notes.y);
  await page.keyboard.up(mod);
  await page.waitForTimeout(300);
  check("AC-52 web app: a path whose file is not there opens nothing", await $eval(() => document.getElementById("file").getAttribute("src")) === fileSrc
    && await $eval(() => document.body.dataset.shown) === "term");

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
  await page.waitForFunction(() => !document.body.classList.contains("wide"));   // the page took the new width (its change event comes later)
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

  // a full-screen program (mc) in a pane wider than the view: its rows stay rows while Wrap is chosen
  // (120 columns: Fit keeps the text above the smallest size here; 200 would be too small to read)
  const mcRows = (cols) => Array.from({ length: 6 }, (_, i) => ({ r: [run("│" + `file-${i}.json`.padEnd(cols / 2 - 2) + "│" + "4.0K".padStart(cols / 2 - 2) + "│", 0)], e: false }));
  const draw = (alt, cols = 120) => { const mc = mcRows(cols); tell({ t: "screen", sid: "S1", full: true, n: mc.length, cols, rows: mc.length, alt, ch: mc.map((l, i) => [i, l]) }); };
  const layoutNow = () => $eval(() => {
    const t = document.getElementById("term"), right = t.getBoundingClientRect().right, rows = [...document.querySelectorAll("#screen .ln")];
    const lh = parseFloat(getComputedStyle(t).lineHeight);
    return { wrap: t.classList.contains("wrap"), oneRow: rows.every((l) => l.getBoundingClientRect().height < lh * 1.5),
      inside: rows.every((l) => l.getBoundingClientRect().right <= right + 1), pressed: document.querySelector("[data-mode=wrap]").getAttribute("aria-pressed") };
  });
  draw(true);
  await page.waitForTimeout(150);
  const full = await layoutNow();
  draw(false);
  await page.waitForTimeout(150);
  const flowing = await layoutNow();
  check("AC-52 web app: in Wrap a full-screen program wider than the view keeps its rows, scaled to the width; Wrap is back when it ends",
    !full.wrap && full.oneRow && full.inside && full.pressed === "true" && flowing.wrap && !flowing.oneRow, JSON.stringify({ full, flowing }));
  draw(true, 80);                                               // a pane that fits the view: re-flowing breaks nothing
  await page.waitForTimeout(150);
  const narrow = await layoutNow();
  check("AC-52 web app: a full-screen program in a pane that fits the view stays in Wrap", narrow.wrap, JSON.stringify(narrow));
  // a coding agent's full-screen mode in the same wide pane: text rows, two rules and a count at the
  // right edge; its rows end at their text, so Wrap re-flows them as it does off the alternate screen
  const agent = [{ r: [run("⏺ Bash(python3 - <<'EOF'")], e: true }, { r: [run("  " + "the owner's long line of output ".repeat(5))], e: true },
    ...Array.from({ length: 6 }, (_, i) => ({ r: [run(`  - a line of the answer ${i}`)], e: true })),
    { r: [run(" ".repeat(186) + "312859 tokens")], e: true }, { r: [run("─".repeat(180) + " a title ─")], e: true },
    { r: [run("❯ "), run(" ", 256)], e: true }, { r: [run("─".repeat(200))], e: true }, { r: [run("  ~/p main dirty:20")], e: true }];
  tell({ t: "screen", sid: "S1", full: true, n: agent.length, cols: 200, rows: agent.length, alt: true, ch: agent.map((l, i) => [i, l]) });
  await page.waitForTimeout(150);
  const text = await layoutNow();
  draw(true);                                                   // mc again: a frame
  await page.waitForTimeout(150);
  const frameAgain = await layoutNow();
  check("AC-52 web app: a full-screen program whose rows are text (a coding agent's full-screen mode) re-flows in Wrap; a frame still shows as Fit",
    text.wrap && !text.oneRow && text.pressed === "true" && !frameAgain.wrap && frameAgain.oneRow && frameAgain.inside, JSON.stringify({ text, frameAgain }));
  // a frame so wide that Fit would shrink the text below the smallest size: Grid at the chosen size, scrolled sideways
  const wall = mcRows(600);
  tell({ t: "screen", sid: "S1", full: true, n: wall.length, cols: 600, rows: wall.length, alt: true, ch: wall.map((l, i) => [i, l]) });
  await page.waitForTimeout(150);
  const huge = await $eval(() => {
    const t = document.getElementById("term"), cs = getComputedStyle(t);
    return { grid: t.classList.contains("grid"), size: parseFloat(cs.fontSize), chosen: parseFloat(cs.getPropertyValue("--size")),
      wider: t.scrollWidth > t.clientWidth, pressed: document.querySelector("[data-mode=wrap]").getAttribute("aria-pressed") };
  });
  draw(true, 80);                                               // as before these checks: on the alternate screen, in a pane that fits
  await page.waitForTimeout(150);
  check("AC-52 web app: a frame too wide for Fit to stay readable shows as Grid at the chosen size, the rest reachable sideways",
    huge.grid && huge.size === huge.chosen && huge.wider && huge.pressed === "true", JSON.stringify(huge));
  // its arrows are application cursor keys (mc takes no key after an ESC [ B); a shell's as before
  const arrows = async () => {
    const from = sent.length;
    await $eval(() => document.getElementById("kbd").focus());
    for (const k of ["ArrowDown", "ArrowLeft", "Shift+ArrowUp"]) await page.keyboard.press(k);
    await page.waitForTimeout(100);
    return sent.slice(from).filter((m) => m.t === "in").map((m) => m.data);
  };
  const inApp = await arrows();
  draw(false, 80);
  await page.waitForTimeout(150);
  const inShell = await arrows();
  check("AC-52 web app: on the alternate screen (mc, vim) the arrows go as ESC O, with a modifier as ESC [ 1;m; off it as ESC [",
    JSON.stringify(inApp) === JSON.stringify(["\x1bOB", "\x1bOD", "\x1b[1;2A"]) && JSON.stringify(inShell) === JSON.stringify(["\x1b[B", "\x1b[D", "\x1b[1;2A"]),
    JSON.stringify({ inApp, inShell }));

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
