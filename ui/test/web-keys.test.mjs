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

test("a tap on the input becomes arrow keys; rows only where asked, at most 10", async () => {
  const { arrowsTo } = await import("../../bridge/fbbridge/web/static/cursor.js");
  assert.equal(arrowsTo({ row: 5, col: 10 }, { row: 5, col: 7 }), "\x1b[D\x1b[D\x1b[D");
  assert.equal(arrowsTo({ row: 5, col: 2 }, { row: 5, col: 4 }, { appCursor: true }), "\x1bOC\x1bOC");
  assert.equal(arrowsTo({ row: 5, col: 2 }, { row: 3, col: 2 }), "", "another row in a shell: nothing");
  assert.equal(arrowsTo({ row: 5, col: 2 }, { row: 4, col: 6 }, { vertical: true }), "\x1b[A\x1b[C\x1b[C\x1b[C\x1b[C");
  assert.equal(arrowsTo({ row: 20, col: 0 }, { row: 5, col: 0 }, { vertical: true }), "", "too far");
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
