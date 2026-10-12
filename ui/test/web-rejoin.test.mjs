// The web app's Wrap mode (AC-52): which hard line ends are a program's own wrap of one line.
import { test } from "node:test";
import assert from "node:assert/strict";
import { glued, inputWraps, wrapsInto } from "../../bridge/fbbridge/web/static/term.js";

const COLS = 131;

test("prose an agent wrapped at the width is joined", () => {
  const a = "  The loader now patches require() before the first module loads, so every dependency sees the guarded version, and our tests";
  assert.equal(a.length, 125);
  assert.equal(wrapsInto(a, "  demo shows it blocking the payload.", COLS), true);   // 125 + 1 + 4 > 127
  assert.equal(wrapsInto("     #current .t { font-size: 15px; line-height: 22px; font-weight: 600; min-width: 0; overflow: hidden; text-overflow:", "     ellipsis; white-space: nowrap; }", COLS), true);
});

test("a short line, a blank line or a box row is never joined", () => {
  assert.equal(wrapsInto("short line", "next", COLS), false);
  assert.equal(wrapsInto("x".repeat(128), "   ", COLS), false);
  assert.equal(wrapsInto("│ " + "x".repeat(126), "│ more", COLS), false);
});

test("an agent's marks, list items and numbered lines start a new line", () => {
  const full = "⏺ Read(/private/tmp/claude-501/" + "x".repeat(97);
  for (const next of ["  ⎿  Read image (11.5KB)", "⏺ Done", "❯ ", "✻ Shimmying… (36s)", "· Thinking", "- item", "2. step",
    "     153:#cursub > span { flex: 0 1 auto; }", "  12 + const a = 1;", "  12 - const a = 0;", "     694-}", "🔧 fix"]) {
    assert.equal(wrapsInto(full, next, COLS), false, next);
  }
});

test("a word longer than the row, broken by the program at its edge, joins without a space; prose and two long words keep it", () => {
  const cols = 100;
  const path = "  /private/tmp/claude-501/-Users-alex-p/18bcc92f-d952-41cd-9660-fc7b2d2f516c/scratchpad/wbdr-shots/drill-results.png";
  assert.equal(glued(path.slice(0, cols), path.slice(cols), cols), true);
  assert.equal(glued("  see " + path.slice(2, cols - 4), path.slice(cols - 4), cols), true, "a long word starting inside the row");
  const prose = "  the retry loop never resets its counter, so the next run starts where the last one left off and";
  assert.equal(glued(prose, "  keeps going.", cols), false);
  const url = "  https://devbox.example/" + "a".repeat(70);
  assert.equal(glued(url + "    ", "  https://devbox.example/b", cols), false, "a row that is not full ends a word");
  const listed = (k) => `  /Users/alex/screenshots/very-long-folder-name-for-the-shots/${"x".repeat(25)}/shot-${k}.png`;
  assert.equal(glued(listed(1), listed(2), 80), false, "two paths of a listing, each on its line");
  assert.equal(glued("  cp /Users/alex/shots/build-2026-10-11/simulator/iphone-17/a.png /Users/alex/x.png", "  /Users/alex/final/home-screen.png", 80), false, "cp a b wrapped at its space");
  assert.equal(glued("  " + "x".repeat(76), "  https://devbox.example/next", 80), false, "an address of its own");
});

// Claude Code's input box at 80 columns, as measured in iTerm2: rows of at most 78 characters
// (its prompt included); a word that did not fit went on the next row, a line the user ended stays.
const ROW = "❯ " + Array(7).fill("abcdefghi").join(" ") + " abcdef";   // 78: full

test("in an agent's input box a row re-joins by Claude Code's own wrap (cols - 2)", () => {
  assert.equal(ROW.length, 78);
  assert.equal(inputWraps(ROW.slice(0, 71), "  abcdefg", 80, true), true);         // 71 + 1 + 7 > 78: wrapped
  assert.equal(inputWraps("❯ " + "w".repeat(70), "  abcde", 80, false), false);  // 72 + 1 + 5 fits: the user's own line end
  assert.equal(wrapsInto("❯ " + "w".repeat(70), "  abcde", 80), true);           // the general rule (cols - 4) glued it
});

test("a space typed at a full row's end leaves the cursor alone on the next row: still the same line", () => {
  assert.equal(inputWraps(ROW, "", 80, true), true);
  assert.equal(inputWraps(ROW, "", 80, false), false);                          // an empty row without the cursor ends it
  assert.equal(inputWraps(ROW.slice(0, 60), "", 80, true), false);              // a short row: a new line the user began
  assert.equal(inputWraps(ROW, "  x", 80, true), true);                         // the next letter: the same line still
});
