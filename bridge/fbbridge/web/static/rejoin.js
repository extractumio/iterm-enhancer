// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Wrap mode's rules (AC-52): which of a program's hard line ends are its own wrap of one line
// (term.js re-joins those rows), and which wraps broke a word (joined without a space).

const BOXED = /^[│┃║╎┆|]|[│┃║╎┆|]$/; // rows of a drawn box: never re-joined

// Lines that start something new: a list item, an agent's mark (⏺ ⎿ ● ❯ ✻ · and other symbols
// and emoji) or a numbered line of a listing or a diff ("151:", "12 +", "694-}").
const STARTS_NEW = /^(?:[-*•+>]\s|\d+[.)]\s|[\u00b7\u2190-\u2bff\u{1f000}-\u{1faff}]|\d+(?::|\s*[-+]))/u;

/** True when line a's hard end is a wrap of the text that goes on in line b; room: the most
 *  characters a row of it holds (a program's prose leaves a few cells free). */
export function wrapsInto(a, b, cols, room = cols - 4) {
  if (!a || !b.trim()) return false;
  if (BOXED.test(a.trim()) || BOXED.test(b.trim())) return false;
  const next = b.trimStart();
  if (STARTS_NEW.test(next)) return false;
  // Right-aligned or centered text is padded in front, which makes a short line look full.
  const indent = (t) => t.length - t.trimStart().length;
  if (indent(a) > cols / 4 || indent(b) > cols / 4) return false;
  const word = next.split(/\s/, 1)[0].length;
  return a.length + 1 + word > room;
}

/** The same in a coding agent's input box, as Claude Code wraps what is typed there (measured):
 *  its rows hold cols - 2 characters, prompt included, and a space typed at a full row's end
 *  puts the cursor alone on the next row, which goes on the same line until a word comes. */
export function inputWraps(a, b, cols, cursor) {
  if (!b.trim()) return cursor && !!a && a.length >= cols - 2;
  return wrapsInto(a, b, cols, cols - 2);
}

const NEW_TOKEN = /^(?:\/|~\/|\.\.?\/|[a-z][\w+.-]*:\/\/)/i;   // how a path or an address starts

// A coding agent's tool output: its first row "  ⎿  ", the rest indented 5
const TOOL_ROW = /^(?: {2}⎿\s{2}| {5})\S/;

/** True when Claude Code cut its tool output's line by characters, not at a space: measured in
 *  iTerm2 (2.1.300), a tool's output shown short ("… +81 lines") fills its rows to cols - 5 and
 *  goes on in the next one wherever that falls ("(root:app 06" / "     40), read by"), so
 *  a path breaks inside even when shorter than the row. A full row ending in a character, then
 *  one going on at the same indentation with a character, is one word. */
function toolCut(a, b, cols) {
  return !!a && !!cols && a.length >= cols - 5 && TOOL_ROW.test(a) && /\S$/.test(a) && /^ {5}\S/.test(b);
}

/** True when the wrap between line a and line b (wrapsInto) broke a word longer than the row:
 *  a program does so only at the row's edge (wrap-ansi's hard mode, as Claude Code's), so a is
 *  full and the word across the break is longer than the row's room. Such rows join without a
 *  space (a long path or address stays one), prose keeps its space; a next row that starts a
 *  path or an address is one of its own. Characters are counted, not cells: a full row with
 *  wide characters reads as short and is not glued (a known limit). */
export function glued(a, b, cols) {
  if (toolCut(a, b, cols)) return true;
  if (!a || !cols || a.length < cols - 4) return false;
  const tail = /\S+$/.exec(a)?.[0], head = /^\S+/.exec(b.trimStart())?.[0];
  if (NEW_TOKEN.test(head ?? "")) return false;                 // a path or address of its own (a listing, cp a b)
  return !!tail && !!head && tail.length + head.length > cols - (a.length - a.trimStart().length) - 4;
}
