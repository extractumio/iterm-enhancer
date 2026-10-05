// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Native iTerm2 controls and installation notices; these always address this Mac.
import { api, apiOrToast, type TermState } from "./api";

const navigation = document.getElementById("find-session")!;
navigation.addEventListener("click", () => {
  void apiOrToast("POST", "/api/ui/open-quickly", { body: {}, host: null });
});

const notice = document.getElementById("setup-notice")!;
let shown = "";
const dismissed = (id: string) => {
  try { return localStorage.getItem("fb.setup.dismissed") === id; } catch { return false; }
};

export function renderSetupNotice(value: TermState["setup_notice"]) {
  const id = value?.id ?? "";
  if (id === shown && notice.classList.contains("on") && !dismissed(id)) return;
  shown = id;
  notice.replaceChildren();
  notice.className = "";
  if (!value || dismissed(id)) return;
  const message = document.createElement("span");
  message.textContent = value.message;
  const close = document.createElement("button");
  close.textContent = "Dismiss";
  close.addEventListener("click", () => {
    try { localStorage.setItem("fb.setup.dismissed", id); } catch { /* private mode */ }
    notice.className = "";
  });
  notice.append(message, close);
  notice.className = "on" + (value.error ? " error" : "");
}

/** The user resized this Toolbelt: make its width the default for new windows (AC-27). */
export function watchToolbeltWidth() {
  let reported = innerWidth, timer = 0;
  addEventListener("resize", () => {
    clearTimeout(timer);
    timer = window.setTimeout(() => {
      if (Math.abs(innerWidth - reported) < 4) return;
      reported = innerWidth;
      void api("POST", "/api/ui/toolbelt-width", { body: {}, host: null }).catch(() => {});
    }, 800);
  });
}
