// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-43/45: notices and native navigation through the private fake bridge.
import { check, cmds, internal, within } from "./e2e_harness.mjs";

export async function sessionTools(page, client) {
  const message = "iTerm2 settings updated: system window restoration.";
  await internal("/internal/setup", { id: "setup-test", message, error: false });
  await within("AC-43 changed settings notice", (ms) => page.waitForSelector("#setup-notice.on", { timeout: ms }));
  check("AC-43 exact informational message", (await page.textContent("#setup-notice")).includes(message));
  await page.click("#setup-notice button");
  await page.reload();
  await page.waitForSelector("#tree .row");
  check("AC-43 dismissed notice stays dismissed on reload", !(await page.locator("#setup-notice").getAttribute("class"))?.includes("on"));
  await internal("/internal/setup", null);
  check("AC-43 no change has no notice", !(await page.locator("#setup-notice").getAttribute("class"))?.includes("on"));

  const before = cmds.length;
  await page.click("#find-session");
  await page.waitForTimeout(400);
  check("AC-45 explicit button queues addressed native menu action",
    cmds.slice(before).some((c) => c.action === "open-quickly" && c.by === client()));
  await internal("/internal/error", { message: "Open Quickly is unavailable", by: client() });
  await within("AC-45 native menu error shown to requesting panel", (ms) =>
    page.waitForFunction(() => document.querySelector("#toast")?.textContent === "Open Quickly is unavailable", { timeout: ms }));
}
