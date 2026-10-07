// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The Files panel and the file viewer inside the web app (AC-53). There the panel is pinned
// to the pane the web page shows (not the focused one) and keeps a workspace of its own;
// files open in the page's File view, which receives them as messages.

import { PROXIED } from "./api";

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

// Header buttons that act on the Mac's iTerm2 (recovery, finding a session, the update chip)
// are hidden in the web app; the proxy refuses what they would ask anyway.
if (PROXIED) document.documentElement.classList.add("web-app");

function tell(message: { type: string; path: string; host?: string | null }): boolean {
  if (!around) return false;
  around.postMessage(message, location.origin);
  return true;
}

/** In the web app a file opens in the page's File view; false when there is no such page. */
export const openOutside = (path: string, host: string | null) => tell({ type: "fb-open", path, host });
/** "Reveal in tree" in the File view shows the file in the page's Files view. */
export const revealOutside = (path: string) => tell({ type: "fb-reveal", path });

/** Take what the page around sends (files to open or reveal), and nothing from anywhere else. */
export function acceptFromPage(handlers: { open: (path: string) => void; reveal: (path: string) => void }) {
  if (!around) return;
  addEventListener("message", (e) => {
    if (e.source !== around || e.origin !== location.origin || typeof e.data?.path !== "string") return;
    if (e.data.type === "fb-open") handlers.open(e.data.path);
    if (e.data.type === "fb-reveal") handlers.reveal(e.data.path);
  });
}
