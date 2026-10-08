// The web app's history blocks: they end only where a paragraph ends, and hold enough lines.
import { test } from "node:test";
import assert from "node:assert/strict";
import { blockCuts } from "../../bridge/fbbridge/web/static/term.js";

const E = (s) => [...s].map((c) => c === "|");   // "|" marks a line that ends its paragraph

test("a block closes at the first paragraph end once it has enough lines", () => {
  assert.deepEqual(blockCuts(E("..|..|.|"), 3), [3, 6]);
  assert.deepEqual(blockCuts(E("||||||"), 2), [2, 4, 6]);
});

test("lines after the last paragraph end stay loose", () => {
  assert.deepEqual(blockCuts(E("...|...."), 2), [4]);
  assert.deepEqual(blockCuts(E("........"), 2), []);
  assert.deepEqual(blockCuts([], 2), []);
});

test("a paragraph longer than a block stays whole up to the most lines a block holds", () => {
  assert.deepEqual(blockCuts(E(".........|.|"), 3), [10]);
  assert.deepEqual(blockCuts(E(".........|.|"), 3, 4), [4, 8, 12]);   // one huge soft-wrapped line is cut
  assert.deepEqual(blockCuts(E("..|......."), 3, 4), [3, 7]);
});
