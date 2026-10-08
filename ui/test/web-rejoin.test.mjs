// The web app's Wrap mode (AC-52): which hard line ends are a program's own wrap of one line.
import { test } from "node:test";
import assert from "node:assert/strict";
import { wrapsInto } from "../../bridge/fbbridge/web/static/term.js";

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
