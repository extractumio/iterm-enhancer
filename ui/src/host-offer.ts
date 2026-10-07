// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A remote host's files (AC-38): the one-time offer to browse them (Enable / Not now) and
// the menu entries to enable later or to take the helper off the host again. The bridge
// does the work over the pane's own ssh; a failure comes back as a bridge-error toast. After
// the Mac's build changed, the bridge updates the helper by itself and this says so (AC-42).

import { apiOrToast, esc, toast, type TermState } from "./api";
import { ask, type MenuEntry } from "./dialogs";

type Remote = NonNullable<TermState["remote"]>;

const act = (action: "enable" | "dismiss" | "remove", host: string) =>
  apiOrToast("POST", `/api/remote/${action}`, { body: { host } });

let told = "";  // the last helper update announced: the state repeats it for a minute

/** Show the offer while the host is new to this bridge run ("ask") or being set up. */
export function renderOffer(el: HTMLElement, r?: Remote) {
  if (r?.updated && told !== `${r.key}\n${r.updated}`) {
    told = `${r.key}\n${r.updated}`;
    toast(`Helper on ${r.name} updated to ${r.updated}`);
  }
  const show = r && (r.state === "ask" || r.state === "enabling");
  el.classList.toggle("on", !!show);
  if (!show) { el.innerHTML = ""; delete el.dataset.at; return; }
  const at = `${r.key}\n${r.state}`;
  if (el.dataset.at === at) return; // states repeat every heartbeat: keep the buttons
  el.dataset.at = at;
  const busy = r.state === "enabling";
  el.innerHTML = `<b>${esc(r.name)} is a remote host.</b>Browse its files here? This copies a small helper ` +
    `(about 6 MB) to ~/.iterm-enhancer on ${esc(r.name)} over your ssh connection; it runs only while you use it.` +
    `<div class="actions"><button class="btn sm primary" data-a="enable"${busy ? " disabled" : ""}>${busy ? "Setting up…" : "Enable"}</button>` +
    `<button class="btn sm" data-a="dismiss"${busy ? " disabled" : ""}>Not now</button></div>`;
  el.onclick = (e) => {
    const a = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-a]")?.dataset.a;
    if (a === "enable" || a === "dismiss") void act(a, r.key);
  };
}

/** Context menu entries for the shown pane's host. */
export function hostEntries(r?: Remote): MenuEntry[] {
  if (!r) return [];
  return ["-", r.state === "ask" || r.state === "dismissed"
    ? { id: "host-enable", label: `Browse Files of ${r.name}…` }
    : { id: "host-remove", label: `Remove Helper from ${r.name}…` }];
}

export async function hostMenu(id: string, r: Remote) {
  if (id === "host-enable") return void act("enable", r.key);
  const choice = await ask(`Remove the helper from ${r.name}?`,
    `This closes the connection and deletes ~/.iterm-enhancer on ${r.name}. Browse its files again any time from this menu.`,
    [{ id: "remove", label: "Remove", danger: true }, { id: "cancel", label: "Cancel", primary: true }]);
  if (choice === "remove") void act("remove", r.key);
}
