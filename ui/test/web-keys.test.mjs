// The web app's key encoder (AC-52): bytes as iTerm2 sends them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { encodeKey } from "../../bridge/fbbridge/web/static/keys.js";

const key = (k, mods = {}, code = "") => ({ key: k, code, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...mods });

test("Enter is CR; Shift and Ctrl with Enter are their own key (CSI u), never CR", () => {
  assert.equal(encodeKey(key("Enter"), false), "\r");
  assert.equal(encodeKey(key("Enter", { shiftKey: true }), false), "\x1b[13;2u");
  assert.equal(encodeKey(key("Enter", { ctrlKey: true }), false), "\x1b[13;5u");
  assert.equal(encodeKey(key("Enter", { shiftKey: true, ctrlKey: true }), false), "\x1b[13;6u");
  assert.equal(encodeKey(key("Enter", { altKey: true }), false), "\x1b\r");
});

test("Shift+Tab, Shift+arrows and application cursor keys", () => {
  assert.equal(encodeKey(key("Tab", { shiftKey: true }), false), "\x1b[Z");
  assert.equal(encodeKey(key("ArrowLeft", { shiftKey: true }), true), "\x1b[1;2D");
  assert.equal(encodeKey(key("ArrowLeft"), false), "\x1b[D");
  assert.equal(encodeKey(key("ArrowLeft"), true), "\x1bOD");
});

test("Cmd shortcuts and plain text are left to the browser", () => {
  assert.equal(encodeKey(key("v", { metaKey: true }), false), null);
  assert.equal(encodeKey(key("a", {}, "KeyA"), false), null);
  assert.equal(encodeKey(key("c", { ctrlKey: true }, "KeyC"), false), "\x03");
});

test("a pasted image's path is one shell word", async () => {
  const { shellWord } = await import("../../bridge/fbbridge/web/static/input.js");
  assert.equal(shellWord("/var/folders/ab/T/iterm-enhancer-paste/20261007-120000-0123abcd.png"),
    "/var/folders/ab/T/iterm-enhancer-paste/20261007-120000-0123abcd.png");
  assert.equal(shellWord("/Users/alex/My Files/a.png"), "'/Users/alex/My Files/a.png'");
  assert.equal(shellWord("/home/o'neil/a.png"), "'/home/o'\\''neil/a.png'");
  assert.equal(shellWord("/tmp/$(rm -rf ~).png"), "'/tmp/$(rm -rf ~).png'");
});

test("a tap on a shell's input becomes ← or → along its row; another row is nothing", async () => {
  const { arrowsTo } = await import("../../bridge/fbbridge/web/static/cursor.js");
  assert.equal(arrowsTo({ row: 5, col: 10 }, { row: 5, col: 7 }), "\x1b[D\x1b[D\x1b[D");
  assert.equal(arrowsTo({ row: 5, col: 2 }, { row: 5, col: 4 }, { appCursor: true }), "\x1bOC\x1bOC");
  assert.equal(arrowsTo({ row: 5, col: 2 }, { row: 3, col: 2 }), "", "another row in a shell: nothing");
  assert.equal(arrowsTo({ row: 5, col: 0 }, { row: 5, col: 121 }), "", "too far");
  assert.equal(arrowsTo(null, { row: 1, col: 1 }), "");
  assert.equal(arrowsTo({ row: 1, col: 1 }, { row: 1, col: 1 }), "");
});

test("groups in the order this browser keeps; new windows last, in iTerm2's order", async () => {
  const { ordered } = await import("../../bridge/fbbridge/web/static/reorder.js");
  const g = (wid) => ({ wid });
  assert.deepEqual(ordered([g("a"), g("b"), g("c"), g("d")], ["c", "a"]).map((x) => x.wid), ["c", "a", "b", "d"]);
  assert.deepEqual(ordered([g("a"), g("b")], []).map((x) => x.wid), ["a", "b"]);
});

test("a tap never moves the cursor far: at most 120 columns", async () => {
  const { arrowsTo } = await import("../../bridge/fbbridge/web/static/cursor.js");
  assert.equal(arrowsTo({ row: 1, col: 0 }, { row: 1, col: 121 }), "");
  assert.equal(arrowsTo({ row: 1, col: 0 }, { row: 1, col: 120 }).length, 120 * 3);
});

test("a tap right of the cursor stops where the typed text ends: a faint suggestion is not typed", async () => {
  const { typedEnd } = await import("../../bridge/fbbridge/web/static/cursor.js");
  // "❯ fix it" typed, the cursor after it, then Claude Code's faint suggestion
  assert.equal(typedEnd({ r: [["❯ fix it", null, null, 0], [" ", null, null, 256], ["run the tests", 8, null, 2]] }), 8);
  // nothing typed: the cursor sits on the suggestion's first letter
  assert.equal(typedEnd({ r: [["❯ ", null, null, 0], ["r", null, null, 256 | 2], ["un the tests", null, null, 2]] }), 1);
  // the cursor moved back into the text: what follows it was typed
  assert.equal(typedEnd({ r: [["❯ fix ", null, null, 0], ["i", null, null, 256], ["t now", null, null, 0]] }), 12);
  // a wide character takes two columns, its right half an empty cell
  assert.equal(typedEnd({ r: [[["❯", " ", "日", "", "x"], null, null, 0]] }), 5);
  assert.equal(typedEnd(undefined), 0);
});

test("the cursor's column on a row, and the row with the cursor moved (as the bridge draws it)", async () => {
  const { cursorCol, withCursor } = await import("../../bridge/fbbridge/web/static/cursor.js");
  const row = { r: [["❯ fix", null, null, 1], [" ", null, null, 256]], e: true };
  assert.equal(cursorCol(row), 5);
  assert.equal(cursorCol({ r: [["❯ fix", null, null, 0]] }), -1);
  assert.equal(cursorCol(undefined), -1);
  assert.deepEqual(withCursor(row, null), { r: [["❯ fix", null, null, 1], [" ", null, null, 0]], e: true }, "none: the flag goes, the style stays");
  assert.deepEqual(withCursor(row, 2).r, [["❯ ", null, null, 1], ["f", null, null, 257], ["ix", null, null, 1], [" ", null, null, 0]]);
  assert.deepEqual(withCursor({ r: [["ab", 3, null, 0]] }, 4).r, [["ab", 3, null, 0], ["  ", null, null, 0], [" ", null, null, 256]], "past the text: padded");
  assert.deepEqual(withCursor({ r: [["ab", 3, null, 0]] }, 2).r, [["ab", 3, null, 0], [" ", null, null, 256]], "right after the text");
  const wide = { r: [[["❯", " ", "日", "", "x"], null, null, 0]] };
  assert.deepEqual(withCursor(wide, 3).r, [[["❯", " "], null, null, 0], [["日", ""], null, null, 256], [["x"], null, null, 0]], "a wide character's two cells together");
  assert.equal(cursorCol(withCursor(wide, 4)), 4);
});
