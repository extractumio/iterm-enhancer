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
