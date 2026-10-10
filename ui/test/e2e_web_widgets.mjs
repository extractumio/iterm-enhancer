// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-56 checks for e2e_panel.mjs: the output widgets of the whole web app (the real page files,
// under the page's own CSP) against a fake bridge: a README printed by cat as Markdown, fenced
// code highlighted, a Mermaid diagram and an image in the history, the full-screen viewer, Raw,
// Copy, the reader's place, a renderer that does not load. The renderers are ui/dist's, as fbd
// serves them through the /fb proxy. Alone: node test/e2e_web_widgets.mjs [chromium|webkit].

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { check, cleanup, results } from "./e2e_harness.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, "../../bridge/fbbridge/web/static");
const DIST = path.resolve(HERE, "../dist");
const CSP = /"Content-Security-Policy": \(("[^)]*")\)/s.exec(fs.readFileSync(path.resolve(STATIC, "../site.py"), "utf8"))[1]
  .split(/"\s*"/).join("").replace(/^"|"$/g, "");
const ORIGIN = "http://web.test";
const TYPES = { html: "text/html", css: "text/css", svg: "image/svg+xml", js: "text/javascript", png: "image/png" };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
const LAYOUT = [{ kind: "window", label: "Window 1", where: "", host: null, wid: "W1", new: "New tab", pool: "", items: [
  { id: "S1", title: "One", sub: ["zsh", "~/p"], index: "", host: null, profile: "Default", agent: null, shell: true, state: "" }] }];
const THEME = { bg: "#1e1e1e", fg: "#d4d4d4", cursor: "#ffffff", cursorText: "#000000", bold: "#ffffff", useBold: false,
  ansi: ["#000", "#c33", "#3c3", "#cc3", "#33c", "#c3c", "#3cc", "#ccc", "#666", "#f66", "#6f6", "#ff6", "#66f", "#f6f", "#6ff", "#fff"], font: null, size: null };

const line = (text, extra = {}) => ({ r: [[text, null, null, 0]], e: true, ...extra });
const PROMPT = "alex@devbox ~/app % ";
const HISTORY = [`${PROMPT}cat README.md`, "# App", "", "A small **tool**.", "", "## Install", "", "```sh", "$ npm install", "```", "",
  "- fast", "- small", "", `${PROMPT}cat flow.mmd`, "graph TD", "  A[Start] --> B[Done]", `${PROMPT}echo hi`, "hi",
  ...Array.from({ length: 40 }, (_, i) => `filler ${i}`)].map((t) => line(t));
const SCREEN = ["Here is the fix:", "```python", "def main():", "    return 1", "```", "Saved /tmp/shots/shot.png and ./b.png",
  "Not there: /tmp/shots/gone.png", "│ in a table: /tmp/shots/shot.png │"];
const FILES = { "/tmp/shots": ["shot.png", "b.png", "notes.txt"] };     // what the fake fbd lists

/** The page against a fake bridge; renderers: false makes /fb/renderers.js fail. */
export async function open(browser, { renderers = true, agent = false, history = HISTORY, screen = SCREEN, cwd = "/tmp/shots", files = FILES, image = PNG,
  device = { viewport: { width: 1200, height: 800 } } } = {}) {
  const opts = { renderers };
  const page = await browser.newPage(device);
  const errors = [], sent = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    window.csp = [];
    document.addEventListener("securitypolicyviolation", (e) => window.csp.push(`${e.violatedDirective} ${e.blockedURI}`));
  });
  await page.route(`${ORIGIN}/**`, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/auth/check") return route.fulfill({ status: 204, body: "" });
    if (p.startsWith("/fb/api/raw")) return route.fulfill({ contentType: "image/png", body: image });
    if (p === "/fb/api/ls") {
      const q = new URL(route.request().url()).searchParams;
      const entries = (files[q.get("path")] ?? []).filter((n) => n.includes(q.get("filter") ?? "")).map((n) => ({ n, k: "f" }));
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ status: "ready", total: entries.length, writable: true, gen: 1, entries }) });
    }
    const [root, rel] = p.startsWith("/fb/") ? [DIST, p.slice(4)] : [STATIC, p === "/" ? "index.html" : p];
    if (p === "/fb/renderers.js" && !opts.renderers) return route.fulfill({ status: 502, body: "fbd is not running" });
    const f = path.join(root, path.normalize(rel));
    if (!f.startsWith(root) || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    let body = fs.readFileSync(f);
    if (p === "/") body = body.toString().replace("__BUILD__", "test");
    return route.fulfill({ contentType: TYPES[f.split(".").pop()] ?? "application/octet-stream", body,
      headers: p === "/" ? { "Content-Security-Policy": CSP } : {} });
  });
  let say = null;
  await page.routeWebSocket(`${ORIGIN.replace("http", "ws")}/ws`, (ws) => {
    say = (m) => ws.send(JSON.stringify(m));
    ws.onMessage((m) => {
      const msg = JSON.parse(String(m));
      sent.push(msg);
      if (msg.t === "sub") {
        say({ t: "theme", sid: "S1", theme: THEME });
        say({ t: "hist", mode: "reset", sid: "S1", first: 0, oldest: 0, truncated: false, lines: history });
        const rows = [...screen.map((t) => (typeof t === "string" ? line(t) : t)), { r: [[PROMPT, null, null, 0], [" ", null, null, 256]], e: true }];
        say({ t: "screen", sid: "S1", full: true, n: rows.length, cols: 80, rows: rows.length, ch: rows.map((r, i) => [i, r]) });
      }
      if (msg.t === "files") say({ t: "files", sid: "S1", key: "S1", cwd, home: "/Users/alex", host: null });
    });
    say({ t: "build", id: "test" });
    say({ t: "layout", groups: agent ? [{ ...LAYOUT[0], items: [{ ...LAYOUT[0].items[0], agent: "claude", shell: false }] }] : LAYOUT });
  });
  await page.goto(`${ORIGIN}/`);
  await page.waitForFunction((n) => document.querySelectorAll("#screen .ln").length === n, screen.length + 1, { timeout: 10000 });
  return { page, errors, sent, say: (m) => say(m), setRenderers: (on) => { opts.renderers = on; } };
}

// What the widgets are: per host row, its kind, mode, rendered body and error.
const widgets = (page) => page.evaluate(() => [...document.querySelectorAll("#term .ln.wg-host")].map((h) => {
  const wg = h.shadowRoot.querySelector(".wg");
  return { kind: wg?.dataset.kind, raw: h.classList.contains("wg-raw"), html: wg?.querySelector(".body")?.innerHTML ?? "",
    err: wg?.querySelector(".err:not([hidden])")?.textContent ?? "", row: h.textContent };
}));
const act = (page, kind, what) => page.evaluate(([kind, what]) => {
  const wg = [...document.querySelectorAll("#term .ln.wg-host")].map((h) => h.shadowRoot.querySelector(".wg")).find((w) => w?.dataset.kind === kind);
  wg.querySelector(what).click();
}, [kind, what]);

export async function webWidgets(browser, name = "") {
  const { page, errors, sent, say } = await open(browser);
  const at = `AC-56 web app${name}:`;
  await page.waitForFunction(() => {
    const hosts = [...document.querySelectorAll("#term .ln.wg-host")];
    return hosts.length >= 3 && hosts.every((h) => h.shadowRoot.querySelector(".body")?.childElementCount)
      && hosts.some((h) => h.shadowRoot.querySelector(".body.code .tok-keyword"));
  }, null, { timeout: 15000 }).catch(() => {});
  let w = await widgets(page);
  const md = w.find((x) => x.kind === "markdown"), code = w.find((x) => x.kind === "code"), dia = w.find((x) => x.kind === "mermaid");
  check(`${at} a README printed by cat is rendered as Markdown (headings, code inside highlighted), its raw rows hidden`,
    md && !md.raw && /<h1[^>]*>App<\/h1>/.test(md.html) && /<li>fast<\/li>/.test(md.html) && /tok-/.test(md.html)
    && await page.evaluate(() => [...document.querySelectorAll("#hist .ln.wg-hid")].some((el) => el.textContent === "## Install")), JSON.stringify(md));
  check(`${at} fenced code on the screen is highlighted in its language`, code && /tok-keyword[^>]*>def</.test(code.html), JSON.stringify(code));
  check(`${at} a Mermaid file is a diagram, an image of its SVG`, dia && /<img class="diagram"[^>]*src="data:image\/svg\+xml/.test(dia.html), JSON.stringify(dia)?.slice(0, 300));
  check(`${at} the rows' own text is unchanged (copy, links and the cursor read it)`,
    await page.evaluate(() => document.querySelector("#hist .ln.wg-host").textContent) === "# App");

  // Raw and back, keeping the reader's place when the widget is above the view
  await page.evaluate(() => { const t = document.getElementById("term"); t.scrollTop = t.scrollHeight; });
  await act(page, "markdown", '[data-act="raw"]');
  w = await widgets(page);
  check(`${at} Raw shows the rows as they were, the bar stays`, w.find((x) => x.kind === "markdown").raw
    && await page.evaluate(() => [...document.querySelectorAll("#hist .ln")].some((el) => el.textContent === "## Install" && el.offsetParent)));
  const place = await page.evaluate(async () => {
    const t = document.getElementById("term"), row = [...document.querySelectorAll("#hist .ln")].find((el) => el.textContent === "filler 20");
    row.scrollIntoView({ block: "start" });
    const before = row.getBoundingClientRect().top;
    [...document.querySelectorAll("#term .ln.wg-host")].find((h) => h.shadowRoot.querySelector('.wg[data-kind="markdown"]'))
      .shadowRoot.querySelector('[data-act="on"]').click();
    await new Promise((r) => requestAnimationFrame(r));
    return { before, after: row.getBoundingClientRect().top, moved: t.scrollTop };
  });
  check(`${at} switching a widget above the view keeps the reader's place`, Math.abs(place.before - place.after) < 2, JSON.stringify(place));

  // Copy: the source, over plain http by the copy command
  await page.evaluate(() => { window.copied = []; document.addEventListener("copy", () => window.copied.push(String(getSelection()) || document.activeElement?.value)); });
  await act(page, "markdown", '[data-act="copy"]');
  const copied = await page.evaluate(() => window.copied);
  check(`${at} Copy copies the Markdown source`, copied.some((c) => c?.startsWith("# App\n\nA small **tool**.")), JSON.stringify(copied));

  // the diagram full screen: wheel zooms around the pointer, Esc closes
  await act(page, "mermaid", "img.diagram");
  await page.waitForSelector("#zoom:not([hidden]) img");
  await page.waitForFunction(() => document.querySelector("#zoom img").style.transform.includes("scale"));
  const t0 = await page.evaluate(() => document.querySelector("#zoom img").style.transform);
  await page.mouse.move(600, 400);
  await page.mouse.wheel(0, -300);
  const t1 = await page.evaluate(() => document.querySelector("#zoom img").style.transform);
  const scale = (t) => Number(/scale\(([\d.]+)\)/.exec(t)?.[1]);
  check(`${at} the diagram opens full screen, fitted, and the wheel zooms in`, scale(t1) > scale(t0) * 1.3, `${t0} → ${t1}`);
  await page.keyboard.press("Escape");
  check(`${at} Esc closes the viewer`, await page.evaluate(() => document.getElementById("zoom").hidden));

  // an image path: a button after it; the image below the row, at most half the terminal
  await page.evaluate(() => { const t = document.getElementById("term"); t.scrollTop = t.scrollHeight; });
  await page.waitForFunction(() => [...document.querySelectorAll("#screen .ln")].some((el) => el.shadowRoot?.querySelectorAll(".wgi button").length === 2));
  const clickedAt = await page.evaluate(() => {
    const row = [...document.querySelectorAll("#screen .ln")].find((el) => el.shadowRoot?.querySelector(".wgi"));
    row.shadowRoot.querySelector(".wgi button").click();
    return row.getBoundingClientRect().top;
  });
  await page.waitForFunction(() => [...document.querySelectorAll("#screen .ln")].some((el) => el.shadowRoot?.querySelector("figure img")?.naturalWidth));
  const opened = await page.evaluate(() => {
    const row = [...document.querySelectorAll("#screen .ln")].find((el) => el.shadowRoot?.querySelector("figure"));
    const f = row.shadowRoot.querySelector("figure").getBoundingClientRect(), t = document.getElementById("term").getBoundingClientRect();
    return { rowAt: row.getBoundingClientRect().top, figH: f.height, inSight: f.bottom <= t.bottom + 1 };
  });
  check(`${at} the line whose image was opened stays on screen, moved only as much as the image below it needs to be in sight`,
    opened.inSight && clickedAt - opened.rowAt >= -1 && clickedAt - opened.rowAt <= opened.figH + 10, JSON.stringify({ clickedAt, ...opened }));
  const fig = await page.evaluate(() => {
    const f = [...document.querySelectorAll("#screen .ln")].map((el) => el.shadowRoot?.querySelector("figure")).find(Boolean);
    const img = f.querySelector("img"), t = document.getElementById("term");
    return { src: img.getAttribute("src"), cap: f.querySelector("figcaption").textContent, w: img.getBoundingClientRect().width,
      h: img.getBoundingClientRect().height, tw: t.clientWidth, th: t.clientHeight };
  });
  check(`${at} the image button shows the file below its row, read through the Files proxy, named in full`,
    fig.src === "/fb/api/raw?path=%2Ftmp%2Fshots%2Fshot.png" && fig.cap === "/tmp/shots/shot.png", JSON.stringify(fig));
  check(`${at} … fitted to half the terminal's width and height at most`, fig.w <= fig.tw / 2 + 1 && fig.h <= fig.th / 2 + 1 && fig.w > 0, JSON.stringify(fig));
  check(`${at} … asking the pane's folder (a relative path is taken from it)`, sent.some((m) => m.t === "files"));
  const layout = await page.evaluate(() => {
    const t = document.getElementById("term"), cs = getComputedStyle(t), r = t.getBoundingClientRect();
    const left = r.left + parseFloat(cs.paddingLeft), right = r.right - parseFloat(cs.paddingRight) - (t.offsetWidth - t.clientWidth);
    const img = [...document.querySelectorAll("#screen .ln")].map((el) => el.shadowRoot?.querySelector("figure img")).find(Boolean).getBoundingClientRect();
    const md = [...document.querySelectorAll("#term .ln.wg-host")].map((h) => h.shadowRoot.querySelector('.wg[data-kind="markdown"]')).find(Boolean).getBoundingClientRect();
    return { imgOff: Math.round((img.left + img.right) / 2 - (left + right) / 2), mdW: Math.round(md.width), termW: Math.round(right - left) };
  });
  check(`${at} an opened image is centered across the terminal, and a Markdown widget is as wide as it`,
    Math.abs(layout.imgOff) <= 1 && Math.abs(layout.mdW - layout.termW) <= 2, JSON.stringify(layout));

  const buttons = await page.evaluate(() => [...document.querySelectorAll("#screen .ln")].map((el) => [el.textContent.trim(), el.shadowRoot?.querySelectorAll(".wgi button").length ?? 0]));
  check(`${at} no image button for a file that is not there, nor in a row of a drawn table`,
    buttons.find(([t]) => t.startsWith("Not there"))?.[1] === 0 && buttons.find(([t]) => t.startsWith("│"))?.[1] === 0, JSON.stringify(buttons));

  // the screen moves on: the code widget follows its rows without being drawn again
  const before = await page.evaluate(() => (window.codeWg = document.querySelector("#screen .ln.wg-host").shadowRoot.querySelector(".wg")) && 1);
  say({ t: "screen", sid: "S1", full: false, n: 10, cols: 80, rows: 10, ch: [[8, line("done")], [9, { r: [[PROMPT, null, null, 0], [" ", null, null, 256]], e: true }]] });
  await page.waitForFunction(() => [...document.querySelectorAll("#screen .ln")].some((el) => el.textContent === "done"));
  check(`${at} a screen region stays a widget while the screen changes below it, not drawn again`,
    before === 1 && await page.evaluate(() => document.querySelector("#screen .ln.wg-host")?.shadowRoot.querySelector(".wg") === window.codeWg));

  // the same file printed twice: a widget each, both drawn
  const twice = [...HISTORY, ...HISTORY.slice(0, 18)];      // up to the next prompt
  say({ t: "hist", mode: "reset", sid: "S1", first: 0, oldest: 0, truncated: false, lines: twice });
  await page.waitForFunction(() => [...document.querySelectorAll("#hist .ln.wg-host")].filter((h) => h.shadowRoot.querySelector('.wg[data-kind="markdown"] h1')).length === 2, null, { timeout: 5000 }).catch(() => {});
  w = await widgets(page);
  check(`${at} a file printed twice gets a widget each, both drawn`,
    w.filter((x) => x.kind === "markdown" && /<h1/.test(x.html)).length === 2, JSON.stringify(w.map((x) => [x.kind, x.html.length])));

  // a light profile: drawn again in light colors (Mermaid's default theme)
  const darkSrc = await page.evaluate(() => [...document.querySelectorAll("#term .ln.wg-host")].map((h) => h.shadowRoot.querySelector("img.diagram")).find(Boolean)?.src);
  say({ t: "theme", sid: "S1", theme: { ...THEME, bg: "#fafafa", fg: "#222222" } });
  await page.waitForFunction((was) => { const src = [...document.querySelectorAll("#term .ln.wg-host")].map((h) => h.shadowRoot.querySelector("img.diagram")).find(Boolean)?.src; return src && src !== was; }, darkSrc, { timeout: 8000 }).catch(() => {});
  w = await widgets(page);
  check(`${at} another profile's colors draw the widgets again (a light one: Mermaid's light theme)`,
    w.filter((x) => x.kind === "markdown" && /<h1/.test(x.html)).length === 2 && w.some((x) => x.kind === "mermaid" && /diagram/.test(x.html))
    && (await page.evaluate(() => [...document.querySelectorAll("#term .ln.wg-host")].map((h) => h.shadowRoot.querySelector("img.diagram")).find(Boolean)?.src)) !== darkSrc);

  // the oldest lines dropped (the server's limit): the region that lost its first line is text again
  say({ t: "hist", mode: "append", sid: "S1", first: twice.length, oldest: 3, truncated: false, lines: [line("more")] });
  await page.waitForFunction(() => [...document.querySelectorAll("#hist .ln")].some((el) => el.textContent === "more"));
  await page.waitForTimeout(200);
  const trimmed = await page.evaluate(() => ({
    shown: [...document.querySelectorAll("#hist .ln")].filter((el) => el.textContent === "## Install").map((el) => !!el.offsetParent),
    md: [...document.querySelectorAll("#hist .ln.wg-host")].filter((h) => h.shadowRoot.querySelector('.wg[data-kind="markdown"]')).length }));
  check(`${at} a region whose first line was dropped shows its other lines as text`,
    trimmed.md === 1 && trimmed.shown.length === 2 && trimmed.shown[0] && !trimmed.shown[1], JSON.stringify(trimmed));

  // a long history: regions far from the view stay text until scrolled near, and drawing them
  // keeps the line at the top of the view where it was
  const filler = Array.from({ length: 3000 }, (_, i) => line(`older ${i}`));
  await page.evaluate(() => { const t = document.getElementById("term"); t.scrollTop = t.scrollHeight; });   // following the output
  say({ t: "hist", mode: "reset", sid: "S1", first: 0, oldest: 0, truncated: false, lines: [...HISTORY.slice(0, 14), line(`${PROMPT}true`), ...filler] });
  await page.waitForFunction(() => document.querySelectorAll("#hist .ln").length > 3000);
  await page.waitForTimeout(400);
  const lazy = await page.evaluate(() => document.querySelectorAll("#hist .ln.wg-host").length);
  const scrolled = await page.evaluate(async () => {
    const t = document.getElementById("term"), row = [...document.querySelectorAll("#hist .ln")].find((el) => el.textContent === "older 40");
    row.scrollIntoView({ block: "start" });
    const before = row.getBoundingClientRect().top;
    await new Promise((r) => setTimeout(r, 700));
    return { before, after: row.getBoundingClientRect().top, hosts: document.querySelectorAll("#hist .ln.wg-host").length,
      drawn: !!document.querySelector("#hist .ln.wg-host")?.shadowRoot.querySelector(".body h1") };
  });
  check(`${at} a region far from the view is not drawn while the end is shown, and is drawn when scrolled near, the line in sight staying put`,
    lazy === 0 && scrolled.hosts === 1 && scrolled.drawn && Math.abs(scrolled.before - scrolled.after) < 2, JSON.stringify({ lazy, ...scrolled }));

  check(`${at} nothing breaks the page's CSP (script-src 'self', no eval) and no page errors`,
    !errors.length && !(await page.evaluate(() => window.csp)).length, JSON.stringify({ errors, csp: await page.evaluate(() => window.csp) }));
  await page.close();

  // a coding agent's pane: a fence right above its input box; typing lines into the box
  // (Shift+Enter) neither joins them to the widget nor draws it again
  const ag = await open(browser, { agent: true });
  const RULE = "─".repeat(80);
  const agentScreen = (typed) => [...["Here:", "```python", "def main():", "    return 1", "```"].map((t) => line(t)), line(RULE),
    ...typed.map((t) => line("> " + t)), { r: [["> ", null, null, 0], [" ", null, null, 256]], e: true }, line(RULE), line("  ? for shortcuts")];
  const sendAgent = (typed) => { const r = agentScreen(typed); ag.say({ t: "screen", sid: "S1", full: true, n: r.length, cols: 80, rows: r.length, ch: r.map((x, i) => [i, x]) }); };
  sendAgent([]);
  await ag.page.waitForFunction(() => document.querySelector("#screen .ln.wg-host")?.shadowRoot.querySelector(".tok-keyword"), null, { timeout: 8000 }).catch(() => {});
  await ag.page.evaluate(() => { window.agentWg = document.querySelector("#screen .ln.wg-host")?.shadowRoot.querySelector(".wg"); });
  for (const typed of [["first"], ["first", "second"], ["first", "second", "third"]]) { sendAgent(typed); await ag.page.waitForTimeout(120); }
  await ag.page.waitForTimeout(300);
  const box = await ag.page.evaluate(() => ({
    same: !!window.agentWg && document.querySelector("#screen .ln.wg-host")?.shadowRoot.querySelector(".wg") === window.agentWg,
    hidden: [...document.querySelectorAll("#screen .ln.wg-hid")].map((el) => el.textContent.trim()) }));
  check(`${at} in a coding agent's pane a widget above the input box stays as it is while lines are typed into the box`,
    box.same && box.hidden.every((t) => !t.startsWith(">") && !t.startsWith("─")), JSON.stringify(box));
  await ag.page.close();

  // fbd not running: the widget says so, and the rows show as text
  const off = await open(browser, { renderers: false });
  await off.page.waitForFunction(() => [...document.querySelectorAll("#term .ln.wg-host")].some((h) => h.shadowRoot.querySelector(".err:not([hidden])")), null, { timeout: 10000 }).catch(() => {});
  w = await widgets(off.page);
  const failed = w.find((x) => x.kind === "markdown");
  check(`${at} a renderer that does not load: the widget says why and its rows show as text (fail loud, lose nothing)`,
    failed?.raw && /renderer did not load/.test(failed.err), JSON.stringify(failed));
  off.setRenderers(true);
  await act(off.page, "markdown", '[data-act="on"]');
  await off.page.waitForFunction(() => [...document.querySelectorAll("#term .ln.wg-host")].some((h) => h.shadowRoot.querySelector('.wg[data-kind="markdown"] h1')), null, { timeout: 8000 }).catch(() => {});
  w = await widgets(off.page);
  check(`${at} … and pressing Markdown once the backend is back draws it`, w.some((x) => x.kind === "markdown" && !x.raw && /<h1/.test(x.html) && !x.err), JSON.stringify(w.find((x) => x.kind === "markdown")));
  await off.page.close();
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const pw = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
  const engine = process.argv[2] ?? "chromium";
  const browser = await pw[engine].launch();
  try { await webWidgets(browser, engine === "chromium" ? "" : ` (${engine})`); } finally { await browser.close(); cleanup(); }
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
