// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs, on the web app's "Merge windows": the real index.html,
// style.css and navtools.js; app.js is replaced by a stub that wires the tools and records
// what the page would send to the bridge.

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { check } from "./e2e_harness.mjs";

const STATIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bridge/fbbridge/web/static");
const ORIGIN = "http://web.test";
const STUB = `import { navTools } from "/navtools.js";
document.getElementById("app").hidden = false;
window.sent = [];
window.tools = navTools({ send: (m) => window.sent.push(m), store: { get: (k, d) => d, set() {} },
  current: () => null, onResize() {} });
window.ready = true;`;

export async function webMerge(browser) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
  await page.route(`${ORIGIN}/**`, (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === "/app.js") return route.fulfill({ contentType: "text/javascript", body: STUB });
    const f = path.join(STATIC, p === "/" ? "index.html" : path.normalize(p));
    if (!f.startsWith(STATIC) || !fs.existsSync(f)) return route.fulfill({ status: 404, body: "" });
    const type = f.endsWith(".html") ? "text/html" : f.endsWith(".css") ? "text/css" : f.endsWith(".svg") ? "image/svg+xml" : "text/javascript";
    return route.fulfill({ contentType: type, body: fs.readFileSync(f) });
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${ORIGIN}/`);
  await page.waitForFunction(() => window.ready);
  const layout = (pools) => page.evaluate((p) => window.tools.showMerge(p.map((pool) => ({ pool }))), pools);

  await layout(["", "conn-1"]);
  check("AC-52 merge: no button when every window holds another kind of session", !(await page.isVisible("#mergewin")));
  await layout(["", "", "conn-1", null]);
  check("AC-52 merge: the button shows when two windows can merge", await page.isVisible("#mergewin"));

  await page.click("#mergewin");
  check("AC-52 merge: the first press asks, sending nothing",
    (await page.isVisible("#mergebox")) && (await page.evaluate(() => window.sent.length)) === 0);
  check("AC-52 merge: the question warns about undo and the Files panel",
    /cannot undo/.test(await page.textContent("#mergebox")) && /save edited files/.test(await page.textContent("#mergebox")));
  await page.click("#mergecancel");
  check("AC-52 merge: Cancel sends nothing", !(await page.isVisible("#mergebox")) && (await page.evaluate(() => window.sent.length)) === 0);

  await page.click("#mergewin");
  await page.click("#mergego");
  const sent = await page.evaluate(() => window.sent);
  check("AC-52 merge: Merge sends one merge request", sent.length === 1 && sent[0].t === "merge" && !(await page.isVisible("#mergebox")), JSON.stringify(sent));

  await page.click("#mergewin");
  await layout(["", "conn-1"]);
  check("AC-52 merge: a layout with nothing to merge hides the button and its question",
    !(await page.isVisible("#mergewin")) && !(await page.isVisible("#mergebox")));
  check("AC-52 merge: no page errors", errors.length === 0, errors.join("; "));
  await page.close();
}
