// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Whole-app checkpoints and one shared recovery report, always addressed to this Mac.
import { api, toast } from "./api";
import { defaultSelection, groups, visibleEntries, type Status as ListStatus } from "./recovery-list";

interface Step { state: string; message: string }
interface Status extends ListStatus {
  instance: string; revision: number;
  enabled: boolean; error: string | null;
  job: { id: string; snapshot: string; status: string; steps: Record<string, Step> } | null;
}
const button = document.getElementById("terminal-recovery") as HTMLButtonElement;
const dialog = document.getElementById("recovery-dialog") as HTMLDialogElement;
const automatic = document.getElementById("recovery-enabled") as HTMLInputElement;
const history = document.getElementById("recovery-history") as HTMLSelectElement;
const save = document.getElementById("recovery-save") as HTMLButtonElement;
const restore = document.getElementById("recovery-restore") as HTMLButtonElement;
const report = document.getElementById("recovery-report")!;
const error = document.getElementById("recovery-error")!;
let status: Status | null = null;
let selected = "";
let pending = false;
let changes = 0;

export function renderRecovery(value: Status) {
  if (status?.instance === value.instance && value.revision < status.revision) return;
  changes++;
  status = value;
  if (!pending) automatic.checked = value.enabled;
  history.replaceChildren(...groups(value).map((g) => {
    const group = document.createElement("optgroup");
    group.label = g.label;
    group.append(...g.items.map((i) => Object.assign(document.createElement("option"), { value: i.id, textContent: i.label })));
    return group;
  }));
  if (value.startup?.phase === "pending") selected = value.startup.snapshot ?? "";
  else if (!visibleEntries(value).some((e) => e.id === selected)) selected = defaultSelection(value);
  history.value = selected;
  const running = value.job?.status === "running";
  const starting = value.startup?.phase === "pending";
  history.disabled = starting;
  button.classList.toggle("active", !!running || starting);
  button.title = running || starting ? "Automatic session window recovery is pending" : "Session window recovery";
  save.disabled = !!running || starting || pending;
  restore.disabled = !!running || pending || !selected || (starting &&
    (selected !== value.startup?.snapshot || value.job?.status !== "interrupted"));
  automatic.disabled = pending;
  restore.textContent = value.job?.snapshot === selected ? "Retry / reconcile" : "Restore selected";
  error.textContent = value.error ?? "";
  report.replaceChildren();
  if (starting) {
    const notice = document.createElement("p");
    notice.textContent = "Automatic recovery pending. New checkpoints are paused to preserve the previous run.";
    report.append(notice);
  } else if (value.startup?.skipped) {
    const notice = document.createElement("p");
    notice.textContent = "Automatic restore skipped after a normal iTerm2 exit. Checkpoints remain available for manual Restore.";
    report.append(notice);
  }
  if (value.job) {
    const summary = document.createElement("p");
    const steps = Object.entries(value.job.steps);
    const issues = steps.filter(([, s]) => s.state === "failed" || s.state === "deviation").length;
    summary.textContent = `Recovery ${value.job.status} · ${steps.length} steps · ${issues} reports to check`;
    report.append(summary);
    for (const [key, step] of steps) {
      const row = document.createElement("div");
      row.className = "recovery-step " + step.state;
      const label = document.createElement("strong");
      label.textContent = step.state;
      row.append(label, document.createTextNode(" · " + step.message));
      row.title = key;
      report.append(row);
    }
  }
}

export async function refreshRecovery() {
  const started = changes;
  try {
    const value = await api<Status>("GET", "/api/recovery", { host: null });
    if (changes === started) renderRecovery(value);
  }
  catch (e) { if (dialog.open) error.textContent = (e as Error).message; }
}
async function action(method: string, path: string, body: unknown) {
  pending = true;
  if (status) renderRecovery(status);
  let failure = "";
  try { await api(method, path, { body, host: null }); await refreshRecovery(); }
  catch (e) { failure = (e as Error).message; toast(failure); }
  finally { pending = false; if (status) renderRecovery(status); if (failure) error.textContent = failure; }
}
button.addEventListener("click", () => {  // each opening starts from the default checkpoint
  selected = "";
  if (status) renderRecovery(status);
  dialog.showModal();
  void refreshRecovery();
});
document.getElementById("recovery-close")!.addEventListener("click", () => dialog.close());
automatic.addEventListener("change", () => void action("PUT", "/api/recovery", { enabled: automatic.checked }));
history.addEventListener("change", () => { selected = history.value; if (status) renderRecovery(status); });
save.addEventListener("click", () => void action("POST", "/api/recovery/save", {}));
restore.addEventListener("click", () => void action("POST", "/api/recovery/restore", { snapshot: selected }));
