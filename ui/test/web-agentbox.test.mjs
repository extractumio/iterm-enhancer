// The web app's view of a coding agent's input box (AC-52): the two rules around the cursor and
// the status lines below them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { inputBox, statusKey } from "../../bridge/fbbridge/web/static/agentbox.js";

// "-" a rule, "." text, " " a blank row
const rows = (s) => [...s].map((c) => ({ rule: c === "-", blank: c === " " }));

test("Claude Code's box: rules around the cursor, the footer below", () => {
  assert.deepEqual(inputBox(rows("....-.-.."), 5), { top: 4, bottom: 6, end: 8 });
  assert.deepEqual(inputBox(rows("..-...-.  "), 4), { top: 2, bottom: 6, end: 7 }, "a few input rows; blank rows end the footer");
  assert.deepEqual(inputBox(rows("..-.-"), 3), { top: 2, bottom: 4, end: 4 }, "no footer");
});

test("no box: no cursor, no rule below or above, the cursor on a rule, or much more below", () => {
  assert.equal(inputBox(rows("..-.-."), null), null);
  assert.equal(inputBox(rows("..-..."), 3), null);
  assert.equal(inputBox(rows("....-."), 2), null);
  assert.equal(inputBox(rows("..-.-."), 2), null);
  assert.equal(inputBox(rows("-.-" + ".".repeat(13)), 1), null, "more than 12 rows below is output, not a footer");
  assert.equal(inputBox(rows("-.-..-.."), 1), null, "a rule in what would be the footer");
  assert.deepEqual(inputBox(rows("-" + ".".repeat(12) + "-"), 12), { top: 0, bottom: 13, end: 13 }, "a rule 12 rows away counts");
  assert.equal(inputBox(rows("-" + ".".repeat(13) + "-"), 13), null, "13 rows away does not");
});

test("a status line is known again when only its numbers changed", () => {
  assert.equal(statusKey("  ~/p ctx:55% lim:-/56% · 1 shell"), statusKey("~/p ctx:61% lim:-/57% · 2 shell"));
  assert.notEqual(statusKey("⏵⏵ bypass permissions on"), statusKey("⏵⏵ accept edits on"));
});
