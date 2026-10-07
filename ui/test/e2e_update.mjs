// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-51 in a browser: the bridge says a newer release exists, the panel shows a small chip
// whose menu copies the upgrade command or sends the user's choice to the bridge.
import { base, check, cmds, internal, within } from "./e2e_harness.mjs";

export async function updateTools(page) {
  const chip = () => page.textContent("#update");
  const shown = () => page.evaluate(() => getComputedStyle(document.getElementById("update")).display !== "none");
  check("AC-51 no chip while no newer release is known", !(await shown()));
  await internal("/internal/update", { latest: "<img src=x onerror=alert(1)>" });
  await page.waitForTimeout(300);
  check("AC-51 text that is not a release tag shows nothing", !(await shown()));
  await internal("/internal/update", { latest: "v9.9.9" });
  await within("AC-51 a newer release shows a chip", (ms) => page.waitForSelector("#update.on", { timeout: ms }));
  check("AC-51 … that names it", (await chip()) === "↑ v9.9.9", await chip());

  await page.click("#update");
  await page.waitForSelector(".ctx .mi");
  const items = await page.$$eval(".ctx .mi", (e) => e.map((m) => m.textContent));
  check("AC-51 the menu says what is available and offers copy, skip and off",
    items.length === 4 && items[0].startsWith("v9.9.9 is available") && items[1] === "Copy upgrade command" && items[2] === "Skip v9.9.9" && items[3] === "Don't check for updates", items.join(" | "));
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: base });
  await page.click('.ctx .mi:has-text("Copy upgrade command")');
  await page.waitForTimeout(200);
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  check("AC-51 copy puts the upgrade command on the clipboard, nothing is run", clip === "~/.iterm-enhancer/bin/iterm-enhancer upgrade" && !cmds.some((c) => /update/.test(c.action)), clip);

  await page.click("#update");
  await page.click('.ctx .mi:has-text("Skip v9.9.9")');
  await page.waitForTimeout(300);
  check("AC-51 skip tells the bridge which version", cmds.some((c) => c.action === "update-skip" && c.version === "v9.9.9"), JSON.stringify(cmds.filter((c) => /update/.test(c.action))));
  await internal("/internal/update", { latest: null });         // the bridge's answer
  await within("AC-51 the chip goes when the bridge says there is nothing to show", (ms) => page.waitForFunction(() => !document.getElementById("update").classList.contains("on"), null, { timeout: ms }));

  await internal("/internal/update", { latest: "v9.9.9" });
  await page.waitForSelector("#update.on");
  await page.click("#update");
  await page.click('.ctx .mi:has-text("Don\'t check for updates")');
  await page.waitForTimeout(300);
  check("AC-51 'don't check' reaches the bridge", cmds.some((c) => c.action === "update-off"));
  await internal("/internal/update", { latest: null });
}
