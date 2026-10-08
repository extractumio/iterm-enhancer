// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Clicks and taps on web addresses and file paths in the terminal (AC-52): the text under the
// pointer, taken with the rows it was wrapped over, and what opening it means.
import { absolutePath, tokenAt } from "./links.js";

// A row continues on the next one when the program wrapped it (no hard end) or the page
// re-joined it (Wrap mode); a re-joined hard end stood for a space.
const continues = (el) => !el.classList.contains("eol") || el.classList.contains("join");
const rowOf = (el) => (el?.classList?.contains("ln") ? el : null);

/** The text position at viewport point (x, y), seen through the keyboard field (it may lie
 *  over the cursor's row, invisible, for iOS's Paste). */
export function caretAt(x, y) {
  const kbd = document.getElementById("kbd");
  const was = kbd?.style.pointerEvents;
  if (kbd) kbd.style.pointerEvents = "none";
  try {
    if (document.caretPositionFromPoint) {
      const p = document.caretPositionFromPoint(x, y);
      return p && { node: p.offsetNode, offset: p.offset };
    }
    const r = document.caretRangeFromPoint?.(x, y);       // Safari
    return r && { node: r.startContainer, offset: r.startOffset };
  } finally {
    if (kbd) kbd.style.pointerEvents = was;
  }
}

/** The address or path at viewport point (x, y) of the terminal, or null. */
export function linkAt(x, y) {
  const at = caretAt(x, y);
  if (!at || at.node.nodeType !== Node.TEXT_NODE) return null;
  const row = at.node.parentElement?.closest(".ln");
  if (!row) return null;
  // the caret falls between characters: the one under the pointer is the one it is on, or before it
  const r = document.createRange();
  r.setStart(at.node, at.offset);
  const box = at.offset < at.node.length ? (r.setEnd(at.node, at.offset + 1), r.getBoundingClientRect()) : null;
  const before = !box || x < box.left ? 1 : 0;
  let first = row;
  while (rowOf(first.previousElementSibling) && continues(first.previousElementSibling)) first = first.previousElementSibling;
  let text = "", offset = -1;
  for (let el = first; el; el = rowOf(el.nextElementSibling)) {
    if (el === row) offset = text.length + offsetIn(row, at.node, at.offset) - before;
    text += el.textContent;
    if (!continues(el)) break;
    if (el.classList.contains("eol")) text += " ";
  }
  return offset < 0 ? null : tokenAt(text, offset);
}

function offsetIn(row, node, offset) {
  const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
  let n = 0;
  for (let t = walk.nextNode(); t; t = walk.nextNode()) {
    if (t === node) return n + offset;
    n += t.length;
  }
  return n;
}

/** Opens a token: an address in a new tab of this browser, a path in File. */
export async function openLink(tok, { paneInfo, openFile, toast }) {
  if (tok.kind === "url") {
    window.open(tok.value, "_blank", "noopener,noreferrer");
    return;
  }
  const pane = await paneInfo();
  if (!pane) return toast("The Mac does not answer.");
  if (pane.error) return toast(pane.error, 6000);
  const path = absolutePath(tok.value, pane.cwd, pane.home);
  if (!path) return toast(`Cannot tell where ${tok.value} is: this session's folder is not known.`, 6000);
  openFile(path, pane.host ?? null);
}

/** Pointer feedback and clicks for a terminal element; `tap(x, y)` is for touch screens. */
export function bindLinks(term, actions) {
  let frame = 0;
  term.addEventListener("mousemove", (e) => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; term.classList.toggle("onlink", !!linkAt(e.clientX, e.clientY)); });
  });
  term.addEventListener("mouseleave", () => term.classList.remove("onlink"));
  term.addEventListener("click", (e) => {
    if (e.button !== 0 || String(getSelection())) return;   // a click that ends a selection opens nothing
    const tok = linkAt(e.clientX, e.clientY);
    if (tok) { e.preventDefault(); void openLink(tok, actions); }
  });
  return (x, y) => {
    const tok = linkAt(x, y);
    if (tok) void openLink(tok, actions);
    return !!tok;
  };
}
