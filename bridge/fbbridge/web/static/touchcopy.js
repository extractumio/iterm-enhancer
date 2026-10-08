// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Selecting and copying terminal text on a touch screen (AC-52). With the keyboard up iOS
// selects nothing on a long press, so input.js selects the word itself; these keep that word
// in sight while the keyboard closes and offer Copy, as iOS shows no menu for a selection the
// page made.
import { selectedText } from "./seltext.js";

/** Keeps the selection at viewport height y for `ms` while the page settles: the keyboard
 *  closing grows the page, which then scrolls to its end (app.js fitViewport runs first). At
 *  the end of the output there is no room to scroll, and the word only stays in sight. */
export function keepSelectionAt(term, y, ms = 1000) {
  const fix = () => {
    const sel = getSelection();
    if (!sel?.rangeCount || sel.isCollapsed || !term.contains(sel.anchorNode)) return;
    const r = sel.getRangeAt(0).getBoundingClientRect();
    if (r.height) term.scrollTop += r.top - y;
  };
  const on = [[window.visualViewport, "resize"], [window.visualViewport, "scroll"], [window, "resize"]].filter(([t]) => t);
  for (const [t, e] of on) t.addEventListener(e, fix);
  requestAnimationFrame(fix);
  setTimeout(() => { for (const [t, e] of on) t.removeEventListener(e, fix); }, ms);
}

/** Shows `button` while text in `term` is selected; a tap copies it with copy(text). The tap
 *  may clear the selection first (iOS does), and the terminal then catches up and moves its
 *  lines, so the text is taken while it is selected (the terminal is paused then). */
export function copyButton(button, term, copy) {
  let kept = "", hide = 0;
  document.addEventListener("selectionchange", () => {
    const sel = getSelection();
    if (sel?.rangeCount && !sel.isCollapsed && term.contains(sel.anchorNode)) {
      kept = selectedText(term) ?? "";
      clearTimeout(hide); hide = 0;
      button.hidden = false;
    } else if (!button.hidden && !hide) {
      hide = setTimeout(() => { hide = 0; kept = ""; button.hidden = true; }, 600);   // a tap on it still lands
    }
  });
  button.addEventListener("mousedown", (e) => e.preventDefault());   // focus stays where it is
  button.addEventListener("click", async () => {
    if (!kept) return;
    await copy(kept);
    getSelection().removeAllRanges();                                  // done: the terminal catches up
  });
}

/** The older copy command for text that is no longer selected: it selects a copy of it. */
export function copyByCommand(text) {
  const el = document.createElement("span");
  el.textContent = text;
  el.style.cssText = "position:fixed;left:-9999px;top:0;white-space:pre;-webkit-user-select:text;user-select:text";
  document.body.append(el);
  getSelection().selectAllChildren(el);
  try { return document.execCommand("copy"); } finally { el.remove(); getSelection().removeAllRanges(); }
}
