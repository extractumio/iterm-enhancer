// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Editing in a terminal program from a phone (AC-52): which screen cell a tap is on, where
// the cursor is, and the arrow keys that move one to the other.
import { caretAt } from "./termlinks.js";

const VS15 = "︎";     // added after symbols for their text form: not a character of the terminal's

/** Characters of `row` before (node, offset), not counting added variation selectors. */
function charsBefore(row, node, offset) {
  const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
  let n = 0;
  for (let t = walk.nextNode(); t; t = walk.nextNode()) {
    const text = t === node ? t.data.slice(0, offset) : t.data;
    n += text.length - text.split(VS15).length + 1;
    if (t === node) return n;
  }
  return n;
}

/** The screen's rows, in order: a paragraph's rows are in a block of their own (term.js). */
const rowsOf = (screen) => [...screen.querySelectorAll(".ln")];

/** {row, col} of the screen cell at viewport point (x, y), or null (scrollback, or no text). */
export function cellAt(screen, x, y) {
  const at = caretAt(x, y);
  if (!at || at.node.nodeType !== Node.TEXT_NODE) return null;
  const row = at.node.parentElement?.closest(".ln");
  if (!row || !screen.contains(row)) return null;
  // the caret falls between characters: the one under the finger is the one it is on, or before it
  const r = document.createRange();
  r.setStart(at.node, at.offset);
  const box = at.offset < at.node.length ? (r.setEnd(at.node, at.offset + 1), r.getBoundingClientRect()) : null;
  const before = !box || x < box.left ? 1 : 0;
  return { row: rowsOf(screen).indexOf(row), col: Math.max(0, charsBefore(row, at.node, at.offset) - before) };
}

/** {row, col} of the cursor on the screen, or null. */
export function cursorCell(screen) {
  const cur = screen.querySelector(".cur");
  const row = cur?.closest(".ln");
  const first = cur && document.createTreeWalker(cur, NodeFilter.SHOW_TEXT).nextNode();
  if (!row || !first) return null;
  return { row: rowsOf(screen).indexOf(row), col: charsBefore(row, first, 0) };
}

const FAINT = 2, CURSOR = 256;

/** The column after the last character the user typed on a row ({r: runs} as render.js gets
 *  it): faint text is a program's suggestion (Claude Code shows its next prompt so, and → takes
 *  it), and the cursor's own cell is where the next character goes, so neither counts. */
export function typedEnd(data) {
  let col = 0, end = 0;
  for (const [text, , , flags] of data?.r ?? []) {
    const cells = Array.isArray(text) ? text : [...text];
    for (const c of cells) {
      col++;
      if (c !== "" && c.trim() && !(flags & (FAINT | CURSOR))) end = col;
    }
  }
  return end;
}

/** The ← or → keys from one cell to another on its row: "" when the move is not one to make (another
 *  row: ↑ and ↓ would recall a shell's history; a coding agent's input moves by its text, boxnav.js). */
export function arrowsTo(from, to, { appCursor = false } = {}) {
  if (!from || !to || to.row !== from.row) return "";
  const dx = to.col - from.col;
  if (Math.abs(dx) > 120) return "";
  return ((appCursor ? "\x1bO" : "\x1b[") + (dx < 0 ? "D" : "C")).repeat(Math.abs(dx));
}
