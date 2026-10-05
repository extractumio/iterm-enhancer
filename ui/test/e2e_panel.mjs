// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Browser end-to-end checks of the panel against a private fbd (no iTerm2 needed).
// Starts fbd on port 47832 with a fake bridge, builds a sandbox folder, drives the UI
// with Playwright (Chromium), and changes files on disk behind the panel's back.
//
//   node test/e2e_panel.mjs            (from ui/; needs `npm run build` and fbd built)
//
// Needs a browser once: npx playwright-core install chromium-headless-shell

import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { manyPanels } from "./e2e_sockets.mjs";
import { sessionTools } from "./e2e_session_tools.mjs";
import { recoveryTools } from "./e2e_recovery.mjs";
import { recoveryUpgrade } from "./e2e_recovery_upgrade.mjs";
import { base, bringBack, check, cleanup, cmds, FBD, internal, PORT, push, restartFbd, results, resume, SB, silence, stopFbd, TOKEN, within } from "./e2e_harness.mjs";

// playwright-core is a devDependency; PLAYWRIGHT_CORE may point at another copy
const { chromium } = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 520, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("dialog", (d) => { errors.push("dialog: " + d.message()); void d.dismiss(); });
let client = "";                                     // the panel's X-FB-Client (errors are addressed to it)
page.on("request", (r) => { client = r.headers()["x-fb-client"] ?? client; });
const toastText = () => page.textContent("#toast");
const outside = [], blocked = [];
const external = (u) => !u.startsWith(`http://127.0.0.1:${PORT}`) && !/^(data|about):/.test(u);
// a CSP-blocked load still emits "request" and then fails with "csp": only finished ones left the machine
page.on("requestfinished", (r) => { if (external(r.url())) outside.push(r.url()); });
page.on("requestfailed", (r) => { if (external(r.url()) && r.failure()?.errorText === "csp") blocked.push(r.url()); });
await page.goto(`${base}/?t=${TOKEN}`);
const row = (p) => `#tree .row[data-p="${SB}/${p}"]`;
const focusInput = async () => {
  await page.waitForSelector(".inline-edit.on input");
  await page.waitForFunction(() => document.activeElement?.matches(".inline-edit input"));
};

try {
  await within("AC-01 tree shows the pane's folder", (ms) => page.waitForSelector(row("app.py"), { timeout: ms }));
  await sessionTools(page, () => client);
  await recoveryTools(page, browser);

  // AC-09 create file (opens in a tab), duplicate name, nested folder
  await page.click("#new-file"); await focusInput();
  await page.keyboard.type("hello.py"); await page.keyboard.press("Enter");
  await within("AC-09 new file created and opened", (ms) => page.waitForSelector('.tab.active .tname:text("hello.py")', { timeout: ms }));
  check("AC-09 file exists and is empty", fs.existsSync(`${SB}/hello.py`) && fs.statSync(`${SB}/hello.py`).size === 0);
  await page.click(row("README.md"));
  await page.click("#new-file"); await focusInput();
  await page.keyboard.type("hello.py"); await page.keyboard.press("Enter");
  await within("AC-09 duplicate name shows inline error", (ms) => page.waitForSelector(".inline-edit.bad", { timeout: ms }));
  check("AC-09 error text", (await page.textContent(".inline-edit .err")).includes("already exists"), await page.textContent(".inline-edit .err"));
  await page.keyboard.press("Escape");
  await page.click("#new-folder"); await focusInput();
  await page.keyboard.type("db/migrations"); await page.keyboard.press("Enter");
  await within("AC-09 nested folder created", (ms) => page.waitForSelector(row("db"), { timeout: ms }));
  check("AC-09 db/migrations on disk", fs.existsSync(`${SB}/db/migrations`));

  // AC-08 edit + save
  await page.click('.tab .tname:text("hello.py")');
  await page.waitForSelector(".cm-content");
  await page.click(".cm-content");
  await page.keyboard.type("import os\n");
  await within("AC-08 dirty marker", (ms) => page.waitForSelector(".tab.dirty", { timeout: ms }));
  await page.keyboard.press("Meta+s");
  await within("AC-08 saved (marker cleared)", (ms) => page.waitForSelector(".tab.active:not(.dirty)", { timeout: ms }));
  check("AC-08 content on disk", fs.readFileSync(`${SB}/hello.py`, "utf8") === "import os\n", JSON.stringify(fs.readFileSync(`${SB}/hello.py`, "utf8")));

  // AC-10 rename with F2; the open tab follows
  await page.click(row("hello.py")); await page.focus("#tree");
  await page.keyboard.press("F2"); await focusInput();
  const pre = await page.evaluate(() => { const i = document.querySelector(".inline-edit input"); return i.value.slice(i.selectionStart, i.selectionEnd); });
  check("AC-10 F2 preselects the name without extension", pre === "hello", pre);
  await page.keyboard.type("greet"); await page.keyboard.press("Enter");
  await within("AC-10 tab follows rename", (ms) => page.waitForSelector('.tab .tname:text("greet.py")', { timeout: ms }));
  check("AC-10 renamed on disk", fs.existsSync(`${SB}/greet.py`) && !fs.existsSync(`${SB}/hello.py`));
  await page.click('.tab .tname:text("greet.py")');
  await within("AC-10 renamed tab shows content", (ms) => page.waitForFunction(() => document.querySelector(".cm-content")?.textContent.includes("import os"), null, { timeout: ms }));

  // AC-13 live refresh
  await page.click(row("app.py"));
  await page.waitForFunction(() => document.querySelector(".cm-content")?.textContent.includes("print(1)"));
  fs.appendFileSync(`${SB}/app.py`, "print(2)\n");
  await within("AC-13 clean tab reloads after external edit", (ms) => page.waitForFunction(() => document.querySelector(".cm-content")?.textContent.includes("print(2)"), null, { timeout: ms }));
  fs.writeFileSync(`${SB}/new.rs`, "");
  await within("AC-13 new file appears in tree", (ms) => page.waitForSelector(row("new.rs"), { timeout: ms }));
  fs.rmSync(`${SB}/new.rs`);
  await within("AC-13 deleted file disappears", (ms) => page.waitForSelector(row("new.rs"), { state: "detached", timeout: ms }));

  // AC-08 conflict: dirty tab + external change → banner → save → dialog → overwrite
  await page.click(".cm-content"); await page.keyboard.press("Meta+End"); await page.keyboard.type("# mine");
  await page.waitForSelector(".tab.dirty");
  fs.appendFileSync(`${SB}/app.py`, "print(3)\n");
  await within("AC-13 dirty tab shows 'Changed on disk'", (ms) => page.waitForSelector('.banner:has-text("Changed on disk")', { timeout: ms }));
  await page.click(".cm-content"); await page.keyboard.press("Meta+s");
  await within("AC-08 conflict dialog", (ms) => page.waitForSelector(".modal", { timeout: ms }));
  check("AC-08 dialog title", (await page.textContent(".modal .mtitle")) === "app.py changed on disk", await page.textContent(".modal .mtitle"));
  const before = fs.readFileSync(`${SB}/app.py`, "utf8");
  check("AC-08 disk untouched until chosen", before.includes("print(3)") && !before.includes("# mine"));
  await page.click(".modal button[data-id=overwrite]");
  await page.waitForSelector(".tab.active:not(.dirty)");
  check("AC-08 overwrite wrote my version", fs.readFileSync(`${SB}/app.py`, "utf8").endsWith("# mine"));

  // AC-08 edits typed while a save is in flight stay unsaved (the PUT is slowed down)
  await page.route("**/api/file?*", async (route) => {
    if (route.request().method() === "PUT") await new Promise((r) => setTimeout(r, 600));
    await route.continue();
  });
  await page.click(".cm-content"); await page.keyboard.press("Meta+End"); await page.keyboard.type("\n# a");
  await page.keyboard.press("Meta+s");
  await page.waitForTimeout(150);
  await page.keyboard.type("\n# typed during save");
  await page.waitForTimeout(900);
  await page.unroute("**/api/file?*");
  check("AC-08 edits during a save keep the tab dirty", !!(await page.$(".tab.active.dirty")));
  check("AC-08 the save wrote only what was sent", fs.readFileSync(`${SB}/app.py`, "utf8").endsWith("# a"));
  await page.keyboard.press("Meta+s");
  await page.waitForSelector(".tab.active:not(.dirty)");
  await page.waitForTimeout(900); // our own save's fs event must not raise "Changed on disk"
  check("AC-13 own save does not show 'Changed on disk'", !(await page.$('.banner:has-text("Changed on disk")')));

  // AC-04 / AC-24 Markdown
  await page.click(row("README.md"));
  await page.waitForSelector(".md-body a");
  const inert = await page.$eval(".md-body", (el) => !el.querySelector("img[src='x']") && el.textContent.includes("<img src=x"));
  check("AC-04 raw HTML shown as text", inert);
  const width = await page.$eval(".md-body img", (i) => new Promise((r) => (i.complete ? r(i.naturalWidth) : ((i.onload = () => r(i.naturalWidth)), (i.onerror = () => r(-1))))));
  check("AC-04 relative image loads", width === 1, `width ${width}`);
  const url = page.url();
  await page.click('.md-body a:text("guide")');
  await within("AC-24 local .md link opens a tab", (ms) => page.waitForSelector('.tab.active .tname:text("guide.md")', { timeout: ms }));
  await page.click('.md-body a:text("readme")');
  await within("AC-24 relative link back", (ms) => page.waitForSelector('.tab.active .tname:text("README.md")', { timeout: ms }));
  check("AC-24 panel never navigated", page.url() === url);

  // AC-29 HTML documents render; links inside Markdown and HTML open other documents
  await page.click('.tab .tname:text("guide.md")');
  await page.click('.md-body a:text("the page")');
  await within("AC-29 md link opens an HTML document rendered", (ms) => page.waitForSelector(".tab.active .tname:text('page.html') >> xpath=/ancestor::div[1]", { timeout: ms }).then(() => page.waitForSelector("iframe.html-doc", { timeout: ms })));
  const frame = page.frameLocator("iframe.html-doc");
  await within("AC-29 HTML content shown", (ms) => frame.locator("h1:text('HTML page')").waitFor({ timeout: ms }));
  const imgW = await frame.locator("img").first().evaluate((i) => new Promise((r) => (i.complete ? r(i.naturalWidth) : ((i.onload = () => r(i.naturalWidth)), (i.onerror = () => r(-1))))));
  check("AC-29 relative image inside HTML loads", imgW === 1, `width ${imgW}`);
  check("AC-29 scripts in HTML do not run", (await page.title()) !== "PWNED");
  await page.waitForTimeout(500);
  check("AC-29 HTML loads nothing from outside fbd", outside.length === 0, outside.join(" "));
  check("AC-29 CSP blocked the external image and CSS url()", blocked.length >= 2, `${blocked.length} blocked`);
  check("AC-29 panel URL keeps no token", !/[?&]t=/.test(page.url()), page.url());
  const sec2Top = await frame.locator("#sec2").evaluate((el) => el.getBoundingClientRect().top);
  check("AC-29 '#sec2' anchor scrolled into view", sec2Top < 800, `top ${Math.round(sec2Top)}`);
  check("AC-29 HTML has a Source toggle", !!(await page.$('#tools button[data-act="source"]')));
  await frame.locator("a:text('to readme')").click();
  await within("AC-29 link inside HTML opens the Markdown document", (ms) => page.waitForSelector('.tab.active .tname:text("README.md")', { timeout: ms }));

  // AC-17 image tab
  await page.click(row("docs")); await page.click(row("docs/logo.png"));
  await within("AC-17 image tab shows size", (ms) => page.waitForFunction(() => /1 × 1/.test(document.querySelector(".imgmeta")?.textContent ?? ""), null, { timeout: ms }));

  // AC-05 per-pane memory: switch to another pane and back
  const expandedBefore = await page.$$eval("#tree .chev.open", (c) => c.length);
  await push("e2eB", `${SB}/src`);
  await within("AC-01 switch pane re-roots", (ms) => page.waitForSelector(row("src/main.rs"), { timeout: ms }));
  check("AC-05 other pane starts with no tabs", (await page.$$(".tab")).length === 0);
  await push("e2eA", SB);
  await within("AC-05 back: tabs restored", (ms) => page.waitForSelector('.tab .tname:text("greet.py")', { timeout: ms }));
  await page.waitForTimeout(500);
  check("AC-05 back: expanded folders restored", (await page.$$eval("#tree .chev.open", (c) => c.length)) === expandedBefore, `${expandedBefore} open`);

  // AC-19 multi-select + AC-11 trash
  await page.click(row("a.log")); await page.click(row("c.log"), { modifiers: ["Shift"] });
  await page.waitForFunction(() => document.querySelectorAll("#tree .row.sel").length > 1, null, { timeout: 2000 }).catch(() => {});
  const selNames = await page.$$eval("#tree .row.sel", (r) => r.map((x) => x.dataset.p.split("/").pop()));
  check("AC-19 ⇧-click selects a range (a.log, app.py, b.log, c.log)", selNames.join() === "a.log,app.py,b.log,c.log", selNames.join());
  await page.focus("#tree"); await page.keyboard.press("Meta+Backspace");
  await page.waitForSelector(".modal");
  check("AC-11 confirm text", (await page.textContent(".modal .mtitle")) === "Move 4 items to Trash?", await page.textContent(".modal .mtitle"));
  await page.click(".modal button[data-id=trash]");
  await within("AC-11 items leave the tree", (ms) => page.waitForSelector(row("b.log"), { state: "detached", timeout: ms }));
  for (let i = 0; i < 20 && ["a", "b", "c"].some((f) => fs.existsSync(`${SB}/${f}.log`)); i++) await page.waitForTimeout(100);
  check("AC-11 moved off disk (to Trash)", !["a", "b", "c"].some((f) => fs.existsSync(`${SB}/${f}.log`)));

  // AC-16 filter
  await page.fill("#filter", "gree");
  await within("AC-16 filter narrows the tree", (ms) => page.waitForFunction(() => document.querySelectorAll("#tree .row[data-p]").length === 1, null, { timeout: ms }));
  await page.fill("#filter", "");

  // AC-32 file-type icons
  await page.waitForSelector(`${row("greet.py")} svg.ico`);   // app.py went to the Trash above
  const color = async (p) => (await page.waitForSelector(`${row(p)} svg.ico`, { state: "attached", timeout: 3000 })).getAttribute("style");
  check("AC-32 code icon in the code color", (await color("greet.py")) === "color:var(--k-code)", await color("greet.py"));
  check("AC-32 text icon in the doc color", (await color("d.log")) === "color:var(--k-doc)", await color("d.log"));
  check("AC-32 folders use the folder color", (await color("src")) === "color:var(--folder)");
  check("AC-32 tabs carry the file's icon", !!(await page.$(".tab svg.ico")));

  // AC-31 expand all, skipping heavy folders; ⌥→ / ⌥← / ⌥-click; collapse all
  await page.click("#collapse");
  await page.click("#expand");
  await within("AC-31 expand all opens nested folders", (ms) => page.waitForSelector(row("src/a/b"), { timeout: ms }));
  const said = await toastText();
  check("AC-31 heavy folders stay collapsed", !(await page.$(`${row("node_modules")} .chev.open`)) && !(await page.$(`${row(".git")} .chev.open`)));
  check("AC-31 toast names the skipped folders", /^Expanded \d+ folders · skipped 2 \((node_modules, \.git|\.git, node_modules)\)$/.test(said), said);
  await page.waitForTimeout(800);
  const wsNow = await (await fetch(`${base}/api/workspace?key=e2eA`, { headers: { "X-FB-Token": TOKEN } })).json();
  check("AC-31 expanded folders are saved", wsNow.expanded.includes(`${SB}/src/a/b`), `${wsNow.expanded.length} saved`);
  await page.click("#collapse");
  await within("AC-31 collapse all", (ms) => page.waitForFunction(() => !document.querySelector("#tree .chev.open"), null, { timeout: ms }));
  await page.click(row("src"));                       // expands src one level and selects it
  await within("AC-31 after collapse all a folder opens one level", (ms) => page.waitForSelector(row("src/a"), { timeout: ms }));
  check("AC-31 … and not its old subfolders", !(await page.$(row("src/a/b"))));
  await page.focus("#tree"); await page.keyboard.press("Alt+ArrowRight");
  await within("AC-31 ⌥→ expands below the folder", (ms) => page.waitForSelector(row("src/a/b"), { timeout: ms }));
  await page.keyboard.press("Alt+ArrowLeft");
  await within("AC-31 ⌥← collapses it all", (ms) => page.waitForSelector(row("src/a"), { state: "detached", timeout: ms }));
  await page.click(`${row("src")} .chev`, { modifiers: ["Alt"] });
  await within("AC-31 ⌥-click on the arrow expands below", (ms) => page.waitForSelector(row("src/a/b"), { timeout: ms }));

  // AC-26 a failed bridge command is shown only in the panel that asked
  const error = (by) => internal("/internal/error", { message: `bridge says no (${by})`, by });
  await error("someone-else");
  await page.waitForTimeout(400);
  check("AC-26 another panel's error is not shown", !(await toastText()).includes("bridge says no"));
  await error(client);
  await within("AC-26 the asking panel shows the bridge error", (ms) => page.waitForFunction((c) => document.getElementById("toast").textContent === `bridge says no (${c})`, client, { timeout: ms }));

  // AC-26 ⌘-click → the bridge gets a "viewer" command with a one-time code
  cmds.length = 0;
  await push("e2eA", SB); // keep the fake bridge "alive"
  await page.click(row("README.md"), { modifiers: ["Meta"] });
  await page.waitForTimeout(500);
  const cmd = cmds.find((c) => c.action === "viewer");
  check("AC-26 ⌘-click asks the bridge for a viewer window", cmd?.path === `${SB}/README.md` && /^[0-9a-f]{24}$/.test(cmd?.code ?? ""), JSON.stringify(cmd));
  check("AC-26 the command names the asking panel", cmd?.by === client && client !== "", `${cmd?.by} vs ${client}`);
  // the viewer page: trades the code, hides the tree, keeps no secret in the URL
  const vp = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  vp.on("pageerror", (e) => errors.push("viewer: " + e.message));
  await vp.goto(`${base}/?v=${cmd.code}&view=${encodeURIComponent(SB + "/README.md")}`);
  await within("AC-26 viewer window renders the file", (ms) => vp.waitForSelector(".md-body h1", { timeout: ms }));
  check("AC-26 viewer has no tree", !(await vp.isVisible("#tree")));
  check("AC-26 URL keeps no code or token", !/[?&](v|t)=/.test(vp.url()), vp.url());
  const again = await fetch(`${base}/ticket?v=${cmd.code}`);
  check("AC-26 the code works once", again.status === 401);
  // the full path, selectable, copied with one click; it follows the active tab
  const barText = () => vp.textContent("#pathbar .path");
  check("AC-26 path bar shows the full path", (await barText()) === `${SB}/README.md`, await barText());
  check("AC-26 page title is the full path", (await vp.title()) === `${SB}/README.md`, await vp.title());
  await internal("/internal/viewer-open", { path: `${SB}/app.py` });
  await within("AC-26 path bar follows the active tab", (ms) => vp.waitForFunction((p) => document.querySelector("#pathbar .path")?.textContent === p, `${SB}/app.py`, { timeout: ms }));
  await vp.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: base });
  await vp.click("#pathbar button");
  await vp.waitForTimeout(200);
  const clip = await vp.evaluate(() => navigator.clipboard.readText());
  check("AC-26 copy puts the full path on the clipboard", clip === `${SB}/app.py`, clip);
  check("AC-26 copy says so", (await vp.textContent("#toast")) === `Copied ${SB}/app.py`, await vp.textContent("#toast"));
  await vp.close();

  // AC-27 a new panel starts from the latest split; an open one keeps its own
  const split = () => page.evaluate(() => getComputedStyle(document.getElementById("app")).getPropertyValue("--split").trim());
  const mine = await split();
  await fetch(base + "/api/prefs", { method: "PUT", headers: { "X-FB-Token": TOKEN, "Content-Type": "application/json" }, body: JSON.stringify({ hidden: true, split: 0.3 }) });
  const np = await browser.newPage({ viewport: { width: 520, height: 900 } });
  await np.goto(`${base}/?t=${TOKEN}`);
  await np.waitForSelector(row("app.py").replace("app.py", "src"));
  check("AC-27 new panel takes the default split", (await np.evaluate(() => getComputedStyle(document.getElementById("app")).getPropertyValue("--split").trim())) === "30%");
  check("AC-27 open panel keeps its own split", (await split()) === mine, mine);
  await np.close();

  // AC-36 a panel follows only its own window
  const bound = (pg) => pg.evaluate(() => sessionStorage.getItem("fb.window"));
  await page.click("#crumbs");                                  // the user acts in the panel → bound for sure
  await within("AC-36 acting in the panel binds it to the key window", (ms) => page.waitForFunction(() => sessionStorage.getItem("fb.window") === "w1", null, { timeout: ms }));
  await push("e2eW2", `${SB}/docs`, "w2", false);               // another window, no Toolbelt there
  await page.waitForTimeout(700);
  check("AC-36 a window without a panel does not move it", !!(await page.$(row("src"))) && !(await page.$(row("docs/guide.md"))));
  await push("e2eW2", `${SB}/docs`, "w2", true);                // another window with its own panel
  await page.waitForTimeout(700);
  check("AC-36 another window's panel state does not move it", !!(await page.$(row("src"))));
  const w2 = await browser.newPage({ viewport: { width: 520, height: 900 } });
  await w2.goto(`${base}/?t=${TOKEN}`);                         // the panel of w2 loads while w2 is key
  await within("AC-36 a new panel claims the key window and shows it", (ms) => w2.waitForSelector(row("docs/guide.md"), { timeout: ms }));
  check("AC-36 … and keeps the claim", (await bound(w2)) === "w2", await bound(w2));
  await page.reload();                                           // an in-page reload while w2 is key
  await within("AC-36 after a reload the panel shows its own window", (ms) => page.waitForSelector(row("src"), { timeout: ms }));
  check("AC-36 … from sessionStorage", (await bound(page)) === "w1", await bound(page));
  const w2b = await browser.newPage({ viewport: { width: 520, height: 900 } });
  await w2b.goto(`${base}/?t=${TOKEN}`);                        // a second load guess for w2 (like a restore)
  await within("AC-36 two load guesses for one window both fall", (ms) => w2.waitForFunction(() => !sessionStorage.getItem("fb.window"), null, { timeout: ms }));
  check("AC-36 … the newer one too", (await bound(w2b)) === null, await bound(w2b));
  await w2.close(); await w2b.close();
  await push("e2eA", SB);

  // AC-34 a short restart: an amber dot, no error, and what changed meanwhile shows up
  let noticed = "";
  const watchNotice = setInterval(() => void page.textContent("#notice.on", { timeout: 50 }).then((t) => { noticed ||= t ?? ""; }).catch(() => {}), 100);
  await restartFbd(1200, () => fs.writeFileSync(`${SB}/during-gap.txt`, "x"));
  await within("AC-34 a file created during the gap appears", (ms) => page.waitForSelector(row("during-gap.txt"), { timeout: ms }));
  clearInterval(watchNotice);
  check("AC-34 no 'Backend not running' for a short gap", !noticed, noticed);
  // a save while fbd is away fails loud and keeps the edit
  await page.click(row("greet.py"));
  await page.waitForSelector(".cm-content");
  await page.click(".cm-content"); await page.keyboard.type("# gap\n");
  await stopFbd();
  await page.keyboard.press("Meta+s");
  await within("AC-34 a save during the gap says so", (ms) => page.waitForFunction(() => document.getElementById("toast").textContent === "Not saved: backend restarting — save again", null, { timeout: ms }));
  check("AC-34 … and the tab stays unsaved", !!(await page.$(".tab.active.dirty")));
  // an upgrade: the unsaved tab holds the reload back, the save lets it happen, in place
  await page.evaluate(() => { window.__before = 1; });
  await bringBack({ FB_BUILD_ID: "e2e-next" });
  await within("AC-34 a dirty panel waits for the save", (ms) => page.waitForFunction(() => document.getElementById("note").textContent.startsWith("Update ready"), null, { timeout: ms }));
  check("AC-34 … without reloading", (await page.evaluate(() => window.__before)) === 1);
  await page.keyboard.press("Meta+s");
  await within("AC-34 after the save the panel reloads", (ms) => page.waitForFunction(() => window.__before === undefined, null, { timeout: ms }), 8000);
  await within("AC-34 … with its tabs and tree", (ms) => page.waitForSelector(`${row("src")}`, { timeout: ms }));
  check("AC-34 … the edited tab is back", !!(await page.$('.tab .tname:text("greet.py")')));
  check("AC-34 … and its window binding", (await bound(page)) === "w1", await bound(page));
  check("AC-34 the saved text is on disk", fs.readFileSync(`${SB}/greet.py`, "utf8").includes("# gap"));
  await page.evaluate(() => { window.__after = 1; });
  await page.waitForTimeout(2500);
  check("AC-34 one reload per build (a page that still differs does not loop)", (await page.evaluate(() => window.__after)) === 1);
  await recoveryUpgrade(page);

  // AC-07 another program on the port while fbd is away: it never sees the token, whether
  // it answers nothing useful or forges the proof; the panel comes back to fbd with its edits
  const internalOverTcp = await fetch(`${base}/internal/state`, { method: "POST", headers: { "Content-Type": "application/json", "X-FB-Bridge": "x" }, body: "{}" });
  check("AC-07 /internal is not served over TCP", internalOverTcp.status === 404);
  await page.click(row("greet.py")); await page.waitForSelector(".cm-content");
  await page.click(".cm-content"); await page.keyboard.type("# kept through a squatter\n");
  for (const forge of [false, true]) {
    const seen = [];
    await stopFbd();
    const squatter = http.createServer((req, res) => {
      seen.push(`${req.url} ${JSON.stringify(req.headers)}`);
      const body = forge && req.url.startsWith("/api/hello") ? JSON.stringify({ proof: "0".repeat(64) }) : "{}";
      res.writeHead(200, { "Content-Type": "application/json" }); res.end(body);
    });
    await new Promise((r) => squatter.listen(PORT, "127.0.0.1", r));
    await page.waitForTimeout(forge ? 4000 : 2500);
    if (forge) check("AC-07 … the panel says it cannot trust the port", (await page.textContent("#notice")).includes("another program holds port"));
    await new Promise((r) => { squatter.close(r); squatter.closeAllConnections(); });
    await bringBack();
    check(`AC-07 a program on the port${forge ? " forging the proof" : ""} never gets the token`, seen.length > 0 && !seen.some((x) => x.includes(TOKEN)), `${seen.length} requests`);
    await within("AC-07 … and the panel is back on fbd", (ms) => page.waitForSelector("#dot:not(.off):not(.wait)", { timeout: ms }), 8000);
  }
  check("AC-07 … with the unsaved edit", (await page.textContent(".cm-content")).includes("# kept through a squatter"));

  // AC-03 only regular files are read; AC-08 a large text file saves
  const t0 = Date.now();
  const zero = await fetch(`${base}/api/raw?path=/dev/zero&t=${TOKEN}`);
  check("AC-03 /dev/zero is refused at once", zero.status === 400 && Date.now() - t0 < 1000 && (await zero.json()).message === "Not a regular file");
  fs.writeFileSync(`${SB}/big.txt`, "x".repeat(9 << 20));
  const big = await (await fetch(`${base}/api/file?path=${encodeURIComponent(`${SB}/big.txt`)}`, { headers: { "X-FB-Token": TOKEN } })).json();
  const put = (text) => fetch(`${base}/api/file?path=${encodeURIComponent(`${SB}/big.txt`)}`, { method: "PUT",
    headers: { "X-FB-Token": TOKEN, "Content-Type": "application/json", "If-Match": "*" }, body: JSON.stringify({ text }) });
  const saved = await put("y".repeat(9 << 20));
  check("AC-08 a 9 MB text file is editable and saves", big.writable && saved.status === 200, `${saved.status}`);
  const huge = await put("\u0001".repeat(11 << 20));
  check("AC-08 a body over the limit is refused as JSON", huge.status === 413 && !!(await huge.json().catch(() => null))?.error, `${huge.status}`);
  fs.rmSync(`${SB}/big.txt`);
  await page.waitForSelector(row("big.txt"), { state: "detached", timeout: 5000 }); // the tree caught up before AC-37 records

  // AC-37 a remote host's files through an agent (here: an agent on this Mac, host "e2ehost",
  // serving the same folder, so local and remote paths are the same strings)
  const agentDir = fs.mkdtempSync(path.join(os.tmpdir(), "fba-"));
  fs.rmSync(agentDir, { recursive: true });             // the agent makes its own private folder
  const agentToken = "agent-token-0123456789abcdef";
  const agent = spawn(FBD, ["--agent", "--socket", `${agentDir}/s`], { env: { ...process.env, FB_LOG: "warn" }, stdio: ["pipe", "pipe", "inherit"] });
  agent.stdin.write(agentToken + "\n");
  const ready = await new Promise((r) => agent.stdout.once("data", (d) => r(String(d))));
  check("AC-37 the agent says it is ready", ready.startsWith("fbd-agent ready "), ready.trim());
  await internal("/internal/remote", { host: "e2ehost", socket: `${agentDir}/s`, token: agentToken });
  // a local tab with unsaved text, then the remote pane
  await push("e2eA", SB);
  await page.waitForSelector(row("greet.py"));
  await page.click(row("greet.py")); await page.waitForSelector(".cm-content");
  await page.click(".cm-content"); await page.keyboard.type("# local only\n");
  const hosts = [];
  page.on("request", (r) => { if (r.url().includes("/api/ls")) hosts.push(r.headers()["x-fb-host"] ?? "-"); });
  await push("e2eR", SB, "w1", true, "e2ehost");
  await within("AC-37 the remote pane's tree shows", (ms) => page.waitForFunction(() => document.getElementById("crumbs").textContent.startsWith("e2ehost:"), null, { timeout: ms }));
  await page.waitForSelector(row("greet.py"));
  check("AC-37 its listings name the host", hosts.length > 0 && hosts.every((h) => h === "e2ehost"), hosts.join(","));
  check("AC-37 the local unsaved tab is not in the remote pane", !(await page.$('.tab .tname:text("greet.py")')));
  await page.click(row("greet.py"));
  await within("AC-37 a remote file opens", (ms) => page.waitForSelector('.tab.active .tname:text("greet.py")', { timeout: ms }));
  check("AC-37 … with the host's content, not the local edit", !(await page.textContent(".cm-content")).includes("# local only"));
  fs.writeFileSync(`${SB}/remote-live.txt`, "x");
  await within("AC-37 a change on the host shows up (agent's watcher)", (ms) => page.waitForSelector(row("remote-live.txt"), { timeout: ms }));
  await page.click(row("remote-live.txt"), { button: "right" });
  await page.waitForSelector(".ctx .mi");
  check("AC-37 no Finder actions for remote files", !(await page.$('.ctx .mi:has-text("Reveal in Finder")')) && !!(await page.$('.ctx .mi:has-text("Copy Path")')));
  await page.keyboard.press("Escape");
  // a confirmation answered after the pane switched hosts acts on nothing (the same path
  // exists on this Mac here, as on a Mac host)
  await page.click(row("remote-live.txt"));
  await page.focus("#tree"); await page.keyboard.press("Meta+Backspace");
  await page.waitForSelector(".modal");
  await push("e2eA", SB);
  await page.waitForFunction(() => !document.getElementById("crumbs").textContent.startsWith("e2ehost:"), null, { timeout: 3000 });
  await page.click(".modal button[data-id=trash]");
  await page.waitForTimeout(400);
  check("AC-37 a dialog confirmed after a host switch trashes nothing", fs.existsSync(`${SB}/remote-live.txt`)
    && (await page.textContent("#toast")).includes("nothing changed"), await page.textContent("#toast"));
  await push("e2eR", SB, "w1", true, "e2ehost");
  await page.waitForFunction(() => document.getElementById("crumbs").textContent.startsWith("e2ehost:"), null, { timeout: 3000 });
  const reveal = await fetch(base + "/api/os/reveal", { method: "POST", headers: { "X-FB-Token": TOKEN, "X-FB-Host": "e2ehost", "Content-Type": "application/json" }, body: JSON.stringify({ path: SB }) });
  check("AC-37 fbd refuses Finder actions on remote files", reveal.status === 400);
  const gone = await fetch(`${base}/api/ls?path=${encodeURIComponent(SB)}`, { headers: { "X-FB-Token": TOKEN, "X-FB-Host": "nohost" } });
  check("AC-37 an unknown host is never served from local files", gone.status === 404);
  // back to the local pane: its unsaved tab is still there, with the local edit
  await push("e2eA", SB);
  await within("AC-37 back on the Mac the unsaved local tab is back", (ms) => page.waitForSelector('.tab.dirty .tname:text("greet.py")', { timeout: ms }));
  await page.click('.tab .tname:text("greet.py")');
  check("AC-37 … with its local edit", (await page.textContent(".cm-content")).includes("# local only"));
  await page.keyboard.press("Meta+s");
  agent.stdin.end();                                      // the ssh connection is gone
  await new Promise((r) => agent.once("exit", r));
  check("AC-37 the agent exits with its connection and leaves no folder", !fs.existsSync(agentDir));
  // the host is gone: its pane must fail loud, never list the same path on this Mac
  await internal("/internal/remote", { host: "e2ehost", socket: null, token: null });
  hosts.length = 0;
  await push("e2eR", SB, "w1", true, "e2ehost");
  await within("AC-37 a disconnected host says so", (ms) => page.waitForFunction(() => document.getElementById("tree").textContent.includes("e2ehost is not connected"), null, { timeout: ms }));
  hosts.length = 0;
  await page.click("#refresh");
  await page.waitForTimeout(600);
  check("AC-37 … and its listings never fall back to local files", hosts.length > 0 && hosts.every((h) => h === "e2ehost") && !(await page.$(row("greet.py"))), hosts.join(","));
  await push("e2eA", SB);

  // AC-38 a remote host is offered once: Enable / Not now; the menu enables later or removes
  const remoteState = (state) => push("e2eO", null, "w1", true, undefined,
    { mode: "remote", note: "remote host devbox", stale: true, remote: { key: "devbox", name: "devbox", state } });
  cmds.length = 0;
  await remoteState("ask");
  await within("AC-38 the panel offers to browse the host", (ms) => page.waitForSelector('#offer.on button[data-a="enable"]', { timeout: ms }));
  check("AC-38 … naming it", (await page.textContent("#offer b")) === "devbox is a remote host.");
  await page.click('#offer button[data-a="enable"]');
  await page.waitForTimeout(300);
  check("AC-38 Enable asks the bridge to set the host up", cmds.some((c) => c.action === "host-enable" && c.host === "devbox" && c.by), JSON.stringify(cmds.at(-1)));
  await remoteState("enabling");
  await within("AC-38 … and says it is setting up", (ms) => page.waitForSelector('#offer button[data-a="enable"][disabled]', { timeout: ms }));
  await remoteState("ask");
  await page.waitForSelector('#offer button[data-a="dismiss"]:not([disabled])');
  await page.click('#offer button[data-a="dismiss"]');
  await page.waitForTimeout(300);
  check("AC-38 Not now tells the bridge", cmds.some((c) => c.action === "host-dismiss" && c.host === "devbox"));
  await remoteState("dismissed");
  await within("AC-38 … and the offer goes", (ms) => page.waitForSelector("#offer.on", { state: "detached", timeout: ms }));
  await page.click("#tree", { button: "right", position: { x: 40, y: 200 } });
  await page.waitForSelector(".ctx .mi");
  check("AC-38 the menu can enable it later", !!(await page.$('.ctx .mi:has-text("Browse Files of devbox")')));
  await page.keyboard.press("Escape");
  await remoteState("up");
  await page.click("#tree", { button: "right", position: { x: 40, y: 200 } });
  await page.click('.ctx .mi:has-text("Remove Helper from devbox")');
  await page.waitForSelector(".modal");
  await page.click(".modal button[data-id=remove]");
  await page.waitForTimeout(300);
  check("AC-38 Remove (confirmed) asks the bridge to take the helper off", cmds.some((c) => c.action === "host-remove" && c.host === "devbox"));
  // AC-42 a helper the bridge updated by itself is announced, and the header says so
  await push("e2eO", null, "w1", true, undefined, { mode: "remote", note: "devbox (helper updated to v9.9.0)", stale: true, remote: { key: "devbox", name: "devbox", state: "up", updated: "v9.9.0" } });
  await within("AC-42 the helper update is announced", (ms) => page.waitForFunction(() => document.getElementById("toast").textContent === "Helper on devbox updated to v9.9.0", null, { timeout: ms }));
  check("AC-42 … and the header says so", (await page.textContent("#note")).includes("helper updated to v9.9.0"));
  await push("e2eA", SB);

  await manyPanels(browser, row);

  // AC-28 an outdated link says so and stops retrying
  const op = await browser.newPage();
  let calls = 0;
  op.on("request", () => calls++);
  await op.goto(`${base}/?t=0000outdated0000`);
  await within("AC-28 outdated link explained", (ms) => op.waitForSelector('#notice.on:has-text("Outdated panel link")', { timeout: ms }));
  const n = calls; await op.waitForTimeout(2500);
  check("AC-28 no retry loop", calls - n <= 1, `${calls - n} requests in 2.5 s`);
  await op.close();

  // AC-30 a silent bridge is reported, and its return clears the note
  silence();
  await within("AC-30 silent bridge: \"Not following iTerm2\"", (ms) => page.waitForSelector('#notice.on:has-text("Not following iTerm2")', { timeout: ms }), 13000);
  const st = await (await fetch(base + "/api/state", { headers: { "X-FB-Token": TOKEN } })).json();
  check("AC-30 /api/state says bridge: false", st.bridge === false, JSON.stringify({ bridge: st.bridge, stale: st.stale }));
  check("AC-30 the tree stays usable", await page.isVisible(row("src")));
  await resume();
  await within("AC-30 the note clears when the bridge is back", (ms) => page.waitForSelector("#notice:not(.on)", { state: "attached", timeout: ms }), 1000);

  check("no page errors or alerts", errors.length === 0, errors.join("; "));
  await page.screenshot({ path: path.join(os.tmpdir(), "fb-e2e-panel.png") });
} finally {
  await browser.close();
  cleanup();
}
const failed = results.filter((r) => !r).length;
console.log(failed ? `FAIL (${failed} of ${results.length})` : `PASS (${results.length} checks)`);
process.exit(failed ? 1 : 0);
