// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A coding agent's input box and footer on its screen (AC-52): Claude Code draws its input
// between two rules around the cursor (the top one may carry the session's title) and a footer
// of status lines below (its "/" and "@" lists open there too). On a phone the page draws the
// box as a panel and keeps the status lines it showed while idle behind a button (term.js,
// style.css). A menu the agent draws between rules too (its questions, a permission) is not an
// input: the box's first row is the prompt. Pure: rows are {rule, blank, txt}, as term.js knows them.

const REACH = 12;        // the most rows between the cursor and a rule of its box
const FOOTER = 12;       // the most rows below the box: status lines, or a list it opened
// the input's first row: "❯" (older Claude Code: ">"), or "!" for a shell command, then a space
// (Claude Code: a no-break one) or nothing (trimmed); a menu's chosen item ("❯ 1. Yes") has a
// plain space and its number
const PROMPT = /^[❯>!](\s|$)/, ITEM = /^[❯>] +\d+\./;

/** {top, bottom, end}: the box's rules are rows top and bottom, its footer bottom+1..end
 *  (end < bottom+1: none); null when the cursor is not in such a box near the screen's end. */
export function inputBox(rows, cursor) {
  if (cursor == null || cursor < 0 || cursor >= rows.length || rows[cursor].rule) return null;
  let top = cursor - 1, bottom = cursor + 1;
  while (top >= 0 && cursor - top < REACH && !rows[top].rule) top--;
  while (bottom < rows.length && bottom - cursor < REACH && !rows[bottom].rule) bottom++;
  if (top < 0 || bottom >= rows.length || !rows[top].rule || !rows[bottom].rule) return null;
  const first = rows[top + 1].txt;
  if (!PROMPT.test(first) || ITEM.test(first)) return null;     // a menu: its ↑ and ↓ are its own
  let end = rows.length - 1;
  while (end > bottom && rows[end].blank) end--;
  if (end - bottom > FOOTER) return null;                 // more below than a footer: not an input box
  for (let i = bottom + 1; i <= end; i++) if (rows[i].rule) return null;
  return { top, bottom, end };
}

/** A status line as it stays while it only counts (context, limits, shells): digits left out. */
export const statusKey = (txt) => txt.trim().replace(/\d+/g, "#");
