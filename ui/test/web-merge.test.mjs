// "Merge windows" in the web app (AC-52): shown only when it would move a tab.
import { test } from "node:test";
import assert from "node:assert/strict";
import { canMerge } from "../../bridge/fbbridge/web/static/navtools.js";

test("two iTerm2 windows, or two windows of one tmux session, can merge", () => {
  assert.equal(canMerge([{ pool: "" }, { pool: "" }]), true);
  assert.equal(canMerge([{ pool: "" }, { pool: "conn-1" }, { pool: "conn-1" }]), true);
});

test("one window of each kind, or the Files viewer beside one window, cannot", () => {
  assert.equal(canMerge([]), false);
  assert.equal(canMerge([{ pool: "" }]), false);
  assert.equal(canMerge([{ pool: "" }, { pool: "conn-1" }, { pool: "conn-2" }]), false);
  assert.equal(canMerge([{ pool: "" }, { pool: null }]), false);
  assert.equal(canMerge([{ pool: "" }, {}]), false);      // a page newer than its bridge shows no button
});
