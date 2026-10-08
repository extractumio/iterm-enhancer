// The web app's copied terminal text: line ends that CSS draws come back as text.
import { test } from "node:test";
import assert from "node:assert/strict";
import { joinLines } from "../../bridge/fbbridge/web/static/seltext.js";

const L = (text, more = {}) => ({ text, eol: true, join: false, cont: false, ...more });

test("hard line ends become newlines; their padding is dropped", () => {
  assert.equal(joinLines([L("$ ls   "), L(""), L("a.txt  b.txt   ")], true), "$ ls\n\na.txt  b.txt");
});

test("a line iTerm wrapped continues without a break", () => {
  assert.equal(joinLines([L("abcdef", { eol: false }), L("ghi")], true), "abcdefghi");
  assert.equal(joinLines([L("abcdef", { eol: false }), L("ghi")], false), "abcdefghi");
});

test("prose re-joined in Wrap keeps one space and loses the indentation; Grid keeps the rows", () => {
  const parts = [L("  The loader patches", { join: true }), L("    require() first", { cont: true })];
  assert.equal(joinLines(parts, true), "  The loader patches require() first");
  assert.equal(joinLines(parts, false), "  The loader patches\n    require() first");
});

test("partial lines at either end and text-style marks", () => {
  assert.equal(joinLines([L("ader"), L("\u2733\ufe0e Done")], true), "ader\n\u2733 Done");
  assert.equal(joinLines([L("only part")], true), "only part");
  assert.equal(joinLines([L("\u2764\ufe0f sent")], true), "\u2764\ufe0f sent");   // the program's own emoji mark stays
});
