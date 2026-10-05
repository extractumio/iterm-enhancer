// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-36 in a browser: a panel follows only its own window, also when it could not bind.
import { base, check, push, SB, TOKEN, within } from "./e2e_harness.mjs";

export async function windowTools(page, browser, row) {
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

  // two panels that could not bind keep their window: another window's state moves neither
  const a = await browser.newPage({ viewport: { width: 520, height: 900 } });
  const b = await browser.newPage({ viewport: { width: 520, height: 900 } });
  await push("e2eW2", `${SB}/docs`, "w2", true);
  await a.goto(`${base}/?t=${TOKEN}`);
  await within("AC-36 an unbound panel shows the window it started on", (ms) => a.waitForSelector(row("docs/guide.md"), { timeout: ms }));
  await b.goto(`${base}/?t=${TOKEN}`);                          // a second guess for w2: both fall
  await within("AC-36 the other panel shows w2 too", (ms) => b.waitForSelector(row("docs/guide.md"), { timeout: ms }));
  await within("AC-36 the guesses collided", (ms) => a.waitForFunction(() => !sessionStorage.getItem("fb.window"), null, { timeout: ms }));
  await push("e2eW3", SB, "w3", true);                          // the focus moves to another window
  await within("AC-36 an unbound panel says it is not linked", (ms) => a.waitForFunction(() => document.getElementById("note").textContent.includes("Click here to follow this window"), null, { timeout: ms }));
  check("AC-36 … and does not re-root", !!(await a.$(row("docs/guide.md"))) && !(await a.$(row("src"))));
  check("AC-36 … nor the other one", !!(await b.$(row("docs/guide.md"))) && !(await b.$(row("src"))));
  await a.click("#crumbs");                                     // a click binds it to the key window
  await within("AC-36 a click binds an unbound panel", (ms) => a.waitForFunction(() => sessionStorage.getItem("fb.window") === "w3", null, { timeout: ms }));
  await within("AC-36 … and shows its window", (ms) => a.waitForSelector(row("src"), { timeout: ms }));
  check("AC-36 … and drops the note", !(await a.textContent("#note")).includes("Click here to follow this window"), await a.textContent("#note"));
  await a.close(); await b.close();
  await push("e2eA", SB);
}
