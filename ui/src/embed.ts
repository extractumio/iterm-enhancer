// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The Files panel and the file viewer inside the web app (AC-53). There the panel is pinned
// to the pane the web page shows (not the focused one) and keeps a workspace of its own;
// files open in the page's File view, which receives them as messages.

import { PROXIED, type Theme } from "./api";

const params = new URLSearchParams(location.search);

/** The pane the panel shows in the web app, or null: then it follows the focused pane. */
export const PIN = PROXIED ? params.get("key") : null;
/** That pane's folder now, and the host its files are on (null: this Mac). */
export const PIN_CWD = params.get("cwd");
export const PIN_HOST = params.get("host");
/** The web panel's own workspace (the Mac's panel for the same pane keeps its tree and tabs):
 *  `web:` + JSON `[host, key]`, the format fbd reads it in (watch_web.rs). */
export const pinKey = (key: string, host: string | null) => `web:${JSON.stringify([host ?? "", key])}`;

const around = PROXIED && window.parent !== window ? window.parent : null;
/** The page around takes its colors from the pane it shows, so it sends them; fbd's theme is the
 *  focused pane's, another one. */
export const THEMED_BY_PAGE = !!around;

// Header buttons that act on the Mac's iTerm2 (recovery, finding a session, the update chip)
// are hidden in the web app; the proxy refuses what they would ask anyway.
if (PROXIED) document.documentElement.classList.add("web-app");

// Files dropped here are not taken (the page uploads files dropped on its terminal), but the
// browser must not open them either: it would leave the page, and unsaved edits with it.
if (around) for (const type of ["dragover", "drop"]) {
  addEventListener(type, (e) => { if ((e as DragEvent).dataTransfer?.types.includes("Files")) e.preventDefault(); });
}

function tell(message: { type: string; path: string; host?: string | null }): boolean {
  if (!around) return false;
  around.postMessage(message, location.origin);
  return true;
}

/** In the web app a file opens in the page's File view; false when there is no such page. */
export const openOutside = (path: string, host: string | null) => tell({ type: "fb-open", path, host });
/** "Reveal in tree" in the File view shows the file in the page's Files view. */
export const revealOutside = (path: string) => tell({ type: "fb-reveal", path });
/** The last file of the File view was closed: the page goes back to the terminal. */
export const emptiedOutside = () => tell({ type: "fb-empty", path: "" });

/** Take what the page around sends (files to open or reveal, its theme), and nothing from anywhere else. */
export function acceptFromPage(handlers: { open: (path: string) => void; reveal: (path: string) => void; theme: (t: Theme) => void }) {
  if (!around) return;
  addEventListener("message", (e) => {
    if (e.source !== around || e.origin !== location.origin) return;
    const d = e.data;
    if (d?.type === "fb-theme" && d.theme && typeof d.theme === "object") return handlers.theme(d.theme);
    if (typeof d?.path !== "string") return;
    if (d.type === "fb-open") handlers.open(d.path);
    if (d.type === "fb-reveal") handlers.reveal(d.path);
  });
}
