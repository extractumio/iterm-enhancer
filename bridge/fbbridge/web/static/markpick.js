// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The [+] beside a selection in the terminal (AC-57), and Copy above it: they stand at the
// terminal's right edge, the [+] level with the selection's end, while text is selected. It lies outside the terminal's rows
// (their taps, copying and line maths stay as they are). The selection is taken while it is
// there: a tap on iOS clears it first, and the terminal then catches up and moves its lines.
import { selectedText } from "./seltext.js";

const KEEP_FOR = 600;          // ms the buttons stay after the selection went (a tap still lands)
const AFTER_COPY = 5000;       // … after Copy: the [+] may still be wanted

// The row a range starts on: in a widget's shadow root (AC-56), the row that holds the widget.
function startRow(range) {
  let node = range.startContainer;
  if (node.nodeType === 1 && !node.closest?.(".ln")) {            // between rows: the next one
    const next = node.childNodes[range.startOffset];
    const row = next?.nodeType === 1 && (next.matches(".ln") ? next : next.querySelector(".ln"));
    if (row) return row;
  }
  while (node) {
    const el = node.nodeType === 1 ? node : node.parentElement;
    const row = el?.closest(".ln");
    if (row) return row;
    node = node.getRootNode?.().host ?? null;
  }
  return null;
}

/** The selected range, also in a widget's shadow root: browsers give the document a collapsed
 *  range by the widget's row there (its text still in toString), and the range itself only to
 *  getComposedRanges given that shadow root (measured with a mouse drag in Playwright's
 *  Chromium and WebKit). */
export function selectedRange(sel, term) {
  const range = sel.getRangeAt(0);
  if (!sel.isCollapsed) return range;
  if (!sel.toString() || !sel.getComposedRanges) return null;
  const roots = [...term.querySelectorAll(".wg-host")].map((h) => h.shadowRoot).filter(Boolean);
  let got;
  try { got = sel.getComposedRanges({ shadowRoots: roots }); } catch { got = sel.getComposedRanges(...roots); }   // the older form
  const s = got?.[0];
  if (!s || s.collapsed) return null;
  const r = document.createRange();
  r.setStart(s.startContainer, s.startOffset);
  r.setEnd(s.endContainer, s.endOffset);
  return r;
}

/** The selection in `term` as {n, text, raw}: the line it starts on and its text as copied
 *  (raw: as it is, for Copy; text: trimmed, for a bookmark); null
 *  when there is none, or it lies in a coding agent's input box or status lines. */
export function selection(term, view) {
  const sel = getSelection();
  const range = sel?.rangeCount ? selectedRange(sel, term) : null;
  if (!range) return null;
  const row = startRow(range);
  if (!row || !term.contains(row) || row.closest(".inbox, .foot")) return null;
  const n = view.lineOf(row);
  const inWidget = range.startContainer.getRootNode() instanceof ShadowRoot;
  const raw = (!inWidget && selectedText(term)) || sel.toString(), text = raw.trim();
  if (n === null || !text) return null;
  const rects = [...range.getClientRects()].filter((r) => r.height > 0);
  return { n, text, raw, rect: rects.at(-1) ?? range.getBoundingClientRect() };
}

/** Shows `button` beside the selection in `term` (within `main`) and `copyButton` above it; a
 *  press calls onPick({n, text}) or onCopy(text) (the text as taken while it was selected). */
export function markPicker({ term, main, button, copyButton, view, onPick, onCopy }) {
  let kept = null, hide = 0, frame = 0, copied = false;
  const show = (on) => { button.hidden = copyButton.hidden = !on; };
  const gone = () => { clearTimeout(hide); hide = 0; kept = null; copied = false; show(false); };
  const place = () => {
    frame = 0;
    if (!kept) return;
    const t = term.getBoundingClientRect(), m = main.getBoundingClientRect(), r = kept.rect;
    const h = button.offsetHeight || 32, gap = 8;
    // the pair inside the terminal; the [+] level with the selection unless an edge is near
    const y = Math.min(Math.max(r.top + r.height / 2 - h / 2, t.top + 4 + h + gap), t.bottom - h - 4);
    const w = button.offsetWidth || 32;              // left of a scroll bar (clientWidth ends there)
    const x = `${Math.round(t.left - m.left + term.clientLeft + term.clientWidth - w - 6)}px`;
    button.style.top = `${Math.round(y - m.top)}px`;
    copyButton.style.top = `${Math.round(y - h - gap - m.top)}px`;
    button.style.left = copyButton.style.left = x;
  };
  const later = () => { if (!frame) frame = requestAnimationFrame(place); };
  let pending = 0;                                 // a drag changes the selection on every move: once a frame
  document.addEventListener("selectionchange", () => { if (!pending) pending = requestAnimationFrame(changed); });
  const changed = () => {
    pending = 0;
    const got = selection(term, view);
    if (got) {
      if (got.raw !== kept?.raw) copied = false;   // another selection (the same one comes back after a copy)
      kept = got;
      clearTimeout(hide); hide = 0;
      show(true);
      place();                                       // placed before they are drawn
    } else if (!button.hidden && !hide) {
      // after Copy the selection may go (a browser's copy, a phone): the [+] stays a while for it
      hide = setTimeout(gone, copied ? AFTER_COPY : KEEP_FOR);
    }
  };
  // the selection moves with the view: where it is now
  term.addEventListener("scroll", () => {
    const sel = getSelection(), range = kept && sel?.rangeCount ? selectedRange(sel, term) : null;
    if (range) {
      const rects = [...range.getClientRects()].filter((r) => r.height > 0);
      if (rects.length) kept.rect = rects.at(-1);
    }
    later();
  }, { passive: true });
  for (const b of [button, copyButton]) b.addEventListener("mousedown", (e) => e.preventDefault());   // the selection stays
  button.addEventListener("click", () => {
    if (!kept) return;
    const { n, text } = kept;
    kept = null; show(false);
    getSelection().removeAllRanges();                                 // the terminal catches up
    onPick({ n, text });
  });
  copyButton.addEventListener("click", () => {
    if (!kept) return;
    copied = true;
    onCopy(kept.raw);                                                  // the selection stays for a bookmark
  });
  // the user moved on: a press in the terminal, a key
  term.addEventListener("pointerdown", () => { if (copied) gone(); });
  document.addEventListener("keydown", () => { if (copied) gone(); });
}
