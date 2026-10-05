// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A recovery dialog survives an upgrade until the user closes it.
import { bringBack, check, stopFbd, within } from "./e2e_harness.mjs";

export async function recoveryUpgrade(page) {
  // The prior fixture simulated another build while serving the original bundle.
  // Start this case with matching UI/backend builds rather than its no-loop latch.
  await stopFbd();
  await bringBack();
  await page.reload();
  await page.click("#terminal-recovery");
  await page.waitForSelector("#recovery-dialog[open]");
  await page.evaluate(() => { window.__recoveryBefore = 1; });
  await stopFbd();
  await bringBack({ FB_BUILD_ID: "e2e-recovery-next" });
  await within("AC-34 open recovery dialog defers upgrade reload", (ms) => page.waitForFunction(() =>
    document.getElementById("note").textContent.startsWith("Update ready"), null, { timeout: ms }));
  check("AC-34 recovery dialog and page survive backend upgrade", await page.isVisible("#recovery-dialog[open]") &&
    (await page.evaluate(() => window.__recoveryBefore)) === 1);
  await page.click("#recovery-close");
  await within("AC-34 closing recovery dialog permits deferred reload", (ms) => page.waitForFunction(() =>
    window.__recoveryBefore === undefined, null, { timeout: ms }), 8000);
  await page.evaluate(() => { window.__recoveryAfter = 1; });
  await page.waitForTimeout(2500);
  check("AC-34 recovery upgrade reload happens once", (await page.evaluate(() => window.__recoveryAfter)) === 1);
}
