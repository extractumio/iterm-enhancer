// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A held delete in the web app (AC-52): characters first, words after a while where the pane takes them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { CHAR, HeldDelete, WORD, WORD_GAP, WORDS_AFTER } from "../../bridge/fbbridge/web/static/heldelete.js";

const clock = () => { const c = { t: 1000 }; c.now = () => c.t; return c; };

test("a keyboard's held delete: characters, then a word every WORD_GAP ms, until it is let go", () => {
  const c = clock(), h = new HeldDelete(c.now);
  assert.equal(h.next(false, true), CHAR);
  const out = [];
  for (c.t += 500; c.t < 1000 + WORDS_AFTER + 300; c.t += 30) out.push(h.next(true, true));
  const words = out.filter((x) => x === WORD).length;
  assert.equal(out.slice(0, 20).every((x) => x === CHAR), true);
  assert.equal(words >= 2 && words <= Math.ceil(300 / WORD_GAP) + 1, true, `${words} words`);
  assert.equal(out.every((x) => [CHAR, WORD, ""].includes(x)), true);
  c.t += 50;
  assert.equal(h.next(false, true), CHAR);                     // a new press starts with a character
});

test("only characters where the pane does not take Ctrl+W as a word (nano: search)", () => {
  const c = clock(), h = new HeldDelete(c.now);
  h.next(false, false);
  for (c.t += 500; c.t < 5000; c.t += 30) assert.equal(h.next(true, false), CHAR);
});

test("a phone's deletes carry no repeat flag: a hold is told by their times", () => {
  const c = clock(), h = new HeldDelete(c.now);
  const out = [];
  for (let i = 0; i < 30; i++, c.t += 100) out.push(h.next(undefined, true));
  assert.equal(out.slice(0, 15).every((x) => x === CHAR), true);
  assert.equal(out.slice(16).every((x) => x === WORD), true, JSON.stringify(out));
  c.t += 1000;                                                  // let go: the next one is a character
  assert.equal(h.next(undefined, true), CHAR);
});

test("quick taps on a phone's delete (a few a second) stay characters: a hold repeats every ~100 ms", () => {
  for (const gap of [200, 250]) {
    const c = clock(), h = new HeldDelete(c.now);
    for (let i = 0; i < 30; i++, c.t += gap) assert.equal(h.next(undefined, true), CHAR, `${gap} ms apart, tap ${i}`);
  }
});
