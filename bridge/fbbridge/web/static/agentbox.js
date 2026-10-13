// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A coding agent's input box and footer on its screen (AC-52): Claude Code draws its input
// between two rules around the cursor (the top one may carry the session's title) and a footer
// of status lines below (its "/" and "@" lists open there too). On a phone the page draws the
// box as a panel and keeps the status lines it showed while idle behind a button (term.js,
// style.css). A menu the agent draws between rules too (its questions, a permission) is not an
// input: the box's first row is the prompt. Pure: rows are {rule, blank, txt}, as term.js knows them.

const FOOTER = 12;       // the most rows below the box: status lines, or a list it opened
// the input's first row: "❯" (older Claude Code: ">"), or "!" for a shell command, then a space
// (Claude Code: a no-break one) or nothing (trimmed); a menu's chosen item ("❯ 1. Yes") has a
// plain space and its number
const PROMPT = /^[❯>!](\s|$)/, ITEM = /^[❯>] +\d+\./;
const NEXT = /^( {2}|$)/;   // the input's next rows: under the prompt's two columns, or blank

/** {top, bottom, end}: the box's rules are rows top and bottom, its footer bottom+1..end
 *  (end < bottom+1: none); null when the cursor is not in such a box near the screen's end.
 *  above(k): the history's k-th row from its end ({rule, blank, txt}, undefined past it), asked
 *  for when the screen has no rule above the cursor: an input taller than the screen has its top
 *  rule and prompt up there (measured), and top counts back from the screen's first row (-1: the
 *  history's last row). */
export function inputBox(rows, cursor, above = () => undefined) {
  if (cursor == null || cursor < 0 || cursor >= rows.length || rows[cursor].rule) return null;
  let top = cursor - 1, bottom = cursor + 1;
  // the rules nearest the cursor, however far: a long input grows the box (the prompt below its
  // top rule and a footer to the screen's end below its bottom one tell it from output)
  while (top >= 0 && !rows[top].rule) top--;
  while (bottom < rows.length && !rows[bottom].rule) bottom++;
  if (bottom >= rows.length) return null;
  let end = rows.length - 1;
  while (end > bottom && rows[end].blank) end--;
  if (end - bottom > FOOTER) return null;                 // more below than a footer: not an input box
  for (let i = bottom + 1; i <= end; i++) if (rows[i].rule) return null;
  const tall = top < 0 ? tallTop(rows, bottom, above) : null;
  if (top < 0 && !tall) return null;
  const first = tall ? tall.txt : rows[top + 1].txt;
  if (!PROMPT.test(first) || ITEM.test(first)) return null;     // a menu: its ↑ and ↓ are its own
  return { top: tall ? tall.top : top, bottom, end };
}

// An input taller than the screen: {top, txt} of its rule and prompt rows, or null. Every row of it on the screen and in the history below the prompt is the input's next
// rows (indented, or blank), so a prompt and rule left far up in the history by an input sent
// long ago are not taken (the output since is not indented so).
function tallTop(rows, bottom, above) {
  for (let i = 1; i < bottom; i++) if (!NEXT.test(rows[i].txt)) return null;
  let k = 0, row = rows[0];
  while (NEXT.test(row.txt)) {
    const up = above(++k);
    if (!up || up.rule) return null;
    row = up;
  }
  if (!above(k + 1)?.rule) return null;
  return { top: -(k + 1), txt: row.txt };
}

/** Where the agent's caret is while the cursor stands below its box and footer: Claude Code puts
 *  the terminal's cursor on its caret after a key, but a redraw of its own (a status line's
 *  refresh, a notice) leaves it on the row after its footer (measured in iTerm2). {row, col}: the
 *  caret last seen in this box ({top, bottom, row, col, txt}: the same rules, its row's text
 *  unchanged), else the end of the input's last row (rows' `end`: the column after its typed
 *  text); null when the cursor is elsewhere. above: as inputBox's. */
export function parkedCaret(rows, cursor, seen, above) {
  if (cursor == null || cursor < 0 || cursor >= rows.length || !rows[cursor].blank) return null;
  const bottom = rows.findLastIndex((r, i) => i < cursor && r.rule);
  if (bottom <= 0) return null;
  const at = inputBox(rows, bottom - 1, above);
  if (!at || at.bottom !== bottom || cursor <= at.end) return null;
  if (seen && seen.top === at.top && seen.bottom === bottom && seen.row > at.top && seen.row < bottom && rows[seen.row].txt === seen.txt)
    return { row: seen.row, col: Math.max(2, seen.col) };
  return { row: bottom - 1, col: Math.max(2, rows[bottom - 1].end ?? 0) };
}

/** A status line as it stays while it only counts (context, limits, shells): digits left out. */
export const statusKey = (txt) => txt.trim().replace(/\d+/g, "#");
