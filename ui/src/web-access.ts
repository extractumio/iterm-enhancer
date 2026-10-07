// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Web access (AC-52): a globe in the header says whether the sessions and files are served to a
// browser on the network, switches it on or off, and gives its addresses. The password is set
// in a terminal (`iterm-filebrowser web password`), never in a panel.

import { apiOrToast, copyText, type TermState } from "./api";
import { menu, type MenuEntry } from "./dialogs";

const button = document.getElementById("web-access")!;
const PASSWORD_COMMAND = "iterm-filebrowser web password";
let web: TermState["web"];

export function renderWeb(value: TermState["web"]) {
  web = value;
  button.hidden = !value;                       // a bridge without web access says nothing
  button.classList.toggle("on", !!value?.enabled);
  button.classList.toggle("warn", !!value?.error);
  button.title = button.ariaLabel = !value ? "" : value.error ? `Web access: ${value.error}`
    : value.enabled ? `Web access is on: ${value.urls[0] ?? ""}` : "Web access is off";
}

button.addEventListener("click", async () => {
  const w = web;
  if (!w) return;
  const at = button.getBoundingClientRect();
  const entries: MenuEntry[] = [
    { id: "info", label: w.enabled ? "Web access is on" : "Web access is off", disabled: true },
    ...(w.error ? [{ id: "error", label: w.error, disabled: true }] : []),
    "-",
    w.enabled ? { id: "off", label: "Turn Web Access Off" } : { id: "on", label: "Turn Web Access On", disabled: !w.password },
    ...(w.password ? [] : [{ id: "password", label: "Copy the Command That Sets Its Password" }]),
    ...(w.enabled && w.urls.length ? ["-" as const, ...w.urls.map((u, i) => ({ id: `url:${i}`, label: `Copy ${u}` }))] : []),
  ];
  button.ariaExpanded = "true";
  const choice = await menu(at.left, at.bottom + 2, entries);
  button.ariaExpanded = "false";
  if (choice === "on" || choice === "off") void apiOrToast("POST", "/api/web", { body: { action: choice }, host: null });
  else if (choice === "password") void copyText(PASSWORD_COMMAND);
  else if (choice?.startsWith("url:")) void copyText(w.urls[Number(choice.slice(4))]);
});
