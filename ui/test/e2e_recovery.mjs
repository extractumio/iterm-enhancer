// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Global controls with a real private backend and a fake API bridge.
import { base, check, cmds, internal, PORT, SB, TOKEN, within } from "./e2e_harness.mjs";

export async function recoveryTools(page, browser) {
  await page.click("#terminal-recovery");
  await page.waitForSelector("#recovery-dialog[open]");
  const dialog = await page.evaluate(() => ({
    title: document.getElementById("recovery-heading").textContent,
    close: [document.getElementById("recovery-close").getAttribute("aria-label"), !!document.querySelector("#recovery-close svg"), document.getElementById("recovery-close").textContent.trim()],
    save: document.getElementById("recovery-save").textContent, restore: document.getElementById("recovery-restore").textContent,
    parts: [...document.querySelectorAll("#recovery-dialog h3")].map((h) => h.textContent),
  }));
  check("AC-50 the dialog is Session Window Recovery, closed by an × icon", dialog.title === "Session Window Recovery" &&
    dialog.close[0] === "Close" && dialog.close[1] && dialog.close[2] === "", JSON.stringify(dialog));
  check("AC-50 … saving a new checkpoint and restoring the selected one are separate parts", dialog.parts.join("|") === "Automatic saving|Restore" &&
    dialog.save === "Save a new checkpoint now" && /^(Restore selected|Retry \/ reconcile)$/.test(dialog.restore), JSON.stringify(dialog));
  check("AC-46 capture and restore enabled on a fresh install", await page.isChecked("#recovery-enabled"));
  await page.uncheck("#recovery-enabled");
  await within("AC-46 explicit opt-out persisted", (ms) => page.waitForFunction(async () => {
    const s = await fetch("/api/recovery", { headers: { "X-FB-Token": sessionStorage.getItem("fb.token") } }).then((r) => r.json());
    return !s.enabled;
  }, { timeout: ms }));
  await page.check("#recovery-enabled");
  await within("AC-46 enable persisted", (ms) => page.waitForFunction(async () => {
    const s = await fetch("/api/recovery", { headers: { "X-FB-Token": sessionStorage.getItem("fb.token") } }).then((r) => r.json());
    return s.enabled;
  }, { timeout: ms }));
  const before = cmds.length;
  await page.click("#recovery-save");
  await within("AC-46 Save now queues a bridge capture", () => page.waitForTimeout(200));
  check("AC-46 capture command is explicit", cmds.slice(before).some((c) => c.action === "save-checkpoint"));
  const snapshot = {
    version: 1, epoch: "test-run", captured: 100, servers: [], warnings: [], windows: [{
      id: "saved-window", frame: { origin: { x: 0, y: 0 }, size: { width: 900, height: 650 } }, fullscreen: false, active: "saved-tab",
      tabs: [{ id: "saved-tab", control: false, active: "saved-pane", tree: { pane: "saved-pane" }, panes: [{
        id: "saved-pane", profile: null, cwd: SB, cwd_status: "known", observed: 100, job: "zsh",
        grid: { width: 80, height: 24 }, connection: { kind: "shell" },
      }] }],
    }],
  };
  const captured = await internal("/internal/recovery/capture", { force: true, snapshot });
  check("AC-46 private complete checkpoint accepted", captured.status === 200);
  const selected = JSON.parse(captured.text).id;
  await within("AC-50 history displays checkpoint", (ms) => page.waitForSelector(`#recovery-history option[value="${selected}"]`, { state: "attached", timeout: ms }));
  const second = await browser.newPage({ viewport: { width: 520, height: 900 } });
  try {
    await second.goto(`${base}/?t=${TOKEN}`);
    await second.click("#terminal-recovery");
    await page.click("#recovery-restore");
    await within("AC-50 both panels observe the same running job", (ms) => second.waitForFunction(() =>
      document.querySelector("#recovery-report")?.textContent.includes("Recovery running"), { timeout: ms }));
    check("AC-50 running job disables duplicate restore in both panels", await page.isDisabled("#recovery-restore") && await second.isDisabled("#recovery-restore"));
    const response = await fetch(base + "/api/recovery", { headers: { "X-FB-Token": TOKEN } });
    const status = await response.json();
    const completed = { ...status.job, status: "complete", steps: { "saved-pane": { state: "deviation", message: "Previous application was not restarted" } } };
    await internal("/internal/recovery/job", completed);
    await within("AC-50 report and retry available", (ms) => page.waitForFunction(() =>
      document.querySelector("#recovery-report")?.textContent.includes("Previous application was not restarted") &&
      !document.querySelector("#recovery-restore")?.disabled, { timeout: ms }));
    const latest = structuredClone(snapshot);
    latest.captured++;
    latest.windows[0].frame.size.width++;
    const latestId = JSON.parse((await internal("/internal/recovery/capture", { force: false, snapshot: latest })).text).id;
    await internal("/internal/recovery/startup", { epoch: "next-test-iterm" });
    await within("AC-50 startup reservation visible in both panels", (ms) => second.waitForFunction(() =>
      document.querySelector("#recovery-report")?.textContent.includes("Automatic recovery pending"), { timeout: ms }));
    check("AC-50 pending startup pauses capture and unrelated restore", await page.isDisabled("#recovery-save") &&
      await page.isDisabled("#recovery-restore"));
    check("AC-50 latest unstable source replaces older stable selection in both panels", await page.inputValue("#recovery-history") === latestId &&
      await second.inputValue("#recovery-history") === latestId);
    const begun = JSON.parse((await internal("/internal/recovery/startup/begin", { epoch: "next-test-iterm" })).text);
    await internal("/internal/recovery/job", { ...begun, status: "interrupted" });
    await within("AC-50 interrupted startup exposes Retry for its reserved source", (ms) => page.waitForFunction(() =>
      !document.querySelector("#recovery-restore")?.disabled, { timeout: ms }));
    await page.click("#recovery-restore");
    await within("AC-50 startup Retry queues the same job", () => page.waitForTimeout(200));
    check("AC-50 Retry uses reserved creation identity", cmds.at(-1)?.job === begun.id);
    await internal("/internal/recovery/job", { ...begun, status: "complete" });
    await internal("/internal/recovery/startup/finish", { epoch: "next-test-iterm" });
    await within("AC-50 completed startup releases Save", (ms) => page.waitForFunction(() =>
      !document.querySelector("#recovery-save")?.disabled, { timeout: ms }));
    const oldStatus = await (await fetch(base + "/api/recovery", { headers: { "X-FB-Token": TOKEN } })).text();
    let release, requested;
    const held = new Promise((resolve) => { release = resolve; });
    const arrived = new Promise((resolve) => { requested = resolve; });
    const delayed = async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      requested();
      await held;
      await route.fulfill({ status: 200, contentType: "application/json", body: oldStatus });
    };
    await page.route("**/api/recovery", delayed);
    await page.click("#recovery-close");
    await page.click("#terminal-recovery");
    await arrived;
    await internal("/internal/recovery/error", { message: "New recovery event arrived before the delayed GET" });
    await within("AC-50 current recovery event wins before a delayed status response", (ms) => page.waitForFunction(() =>
      document.querySelector("#recovery-error")?.textContent.includes("New recovery event"), { timeout: ms }));
    release();
    await page.waitForTimeout(200);
    check("AC-50 delayed GET cannot roll recovery state backward", (await page.textContent("#recovery-error")).includes("New recovery event"));
    await page.unroute("**/api/recovery", delayed);
    await internal("/internal/recovery/error", { message: "" });
    await internal("/internal/recovery/exit", { epoch: "next-test-iterm" });
    const skipped = JSON.parse((await internal("/internal/recovery/startup", { epoch: "next-test-iterm:reopened" })).text);
    check("AC-50 normal same-boot launch skips automatic recovery", skipped.skipped && skipped.phase === "done");
    await within("AC-50 normal exit explains manual Restore in both panels", (ms) => second.waitForFunction(() =>
      document.querySelector("#recovery-report")?.textContent.includes("Automatic restore skipped"), { timeout: ms }));
    check("AC-50 normal exit keeps automatic saving and manual Restore enabled", await page.isChecked("#recovery-enabled") &&
      !await page.isDisabled("#recovery-save") && !await page.isDisabled("#recovery-restore"));
    await second.click("#recovery-close");
    await page.click("#recovery-close");
  } finally { await second.close(); }
}
