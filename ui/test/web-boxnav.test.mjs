// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Moving in a coding agent's input by the page's lines (AC-52): the input's text offsets as
// Claude Code steps over them (measured in iTerm2).
import { test } from "node:test";
import assert from "node:assert/strict";
import { layout, lines, listOpen, nearest, stepKeys, typedLength } from "../../bridge/fbbridge/web/static/boxnav.js";

const offsetOf = (lay, row, col) => lay[row].start + Math.max(0, col - 2);

test("rows of the input: a wrap at a space and a line the user ended are one step, a broken word none", () => {
  // "❯ " + 69 characters (wrapped at a space), "  abcdefg" (a line ended), "  xy"
  const lay = layout([{ len: 71, glue: false }, { len: 9, glue: false }, { len: 4, glue: false }]);
  assert.deepEqual(lay, [{ start: 0, len: 69 }, { start: 70, len: 7 }, { start: 78, len: 2 }]);
  assert.equal(offsetOf(lay, 1, 2), 70);                 // the next row's first character: one step after the space
  assert.equal(offsetOf(lay, 0, 71), 69);                // the cursor on the space at the wrap
  assert.equal(offsetOf(lay, 2, 6), 82);                 // after two spaces typed at the end: the cursor's own column counts
  assert.equal(offsetOf(lay, 2, 4), 80);                 // the end
  const long = layout([{ len: 78, glue: true }, { len: 6, glue: false }]);
  assert.equal(offsetOf(long, 1, 2) - offsetOf(long, 0, 77), 1, "a word broken at the edge goes on without a step between");
});

test("typed text ends before a faint suggestion; a wide character's half and an emoji's halves are one character (measured: ← steps over 😀 once)", () => {
  assert.equal(typedLength({ r: [["❯\u00a0a😀b", null, null, 0]] }), 5);
  assert.equal(typedLength({ r: [["❯\ufe0e\u00a0ab", null, null, 0]] }), 4, "the page's text form of ❯ is part of it");
  assert.equal(typedLength({ r: [["❯ ", null, null, 0], ["Try \"fix\"", null, null, 2]] }), 1);
  assert.equal(typedLength({ r: [["❯ ab", null, null, 0], [" ", null, null, 256]] }), 4);
  assert.equal(typedLength({ r: [[["❯", " ", "漢", "", "x"], null, null, 0]] }), 4);
});

test("steps are ← or → keys, in the cursor keys' mode", () => {
  assert.equal(stepKeys(3, false), "\x1b[C\x1b[C\x1b[C");
  assert.equal(stepKeys(-2, true), "\x1bOD\x1bOD");
  assert.equal(stepKeys(0, false), "");
});

test("a list of the agent's is open while the word at the cursor starts with @, or the input is a / command so far", () => {
  assert.equal(listOpen("look at @", false), true);
  assert.equal(listOpen("look at @src/ma", false), true);
  assert.equal(listOpen("mail@devbox.example done", false), false);
  assert.equal(listOpen("/rev", true), true);
  assert.equal(listOpen("/review this", true), false);
  assert.equal(listOpen("see /usr/bin", true), false);
  assert.equal(listOpen("/rev", false), false, "a / on a later row is text");
});

test("the page's lines: positions grouped by their top, the nearest by x", () => {
  const ps = [{ o: 0, x: 10, top: 100, h: 16 }, { o: 1, x: 20, top: 101, h: 16 }, { o: 2, x: 10, top: 116, h: 16 }, { o: 3, x: 30, top: 116, h: 16 }];
  const ls = lines(ps);
  assert.deepEqual(ls.map((l) => l.map((p) => p.o)), [[0, 1], [2, 3]]);
  assert.equal(nearest(ls[1], 22).o, 3);
  assert.equal(nearest(ls[1], 20).o, 2, "the earlier of two as near");
});
