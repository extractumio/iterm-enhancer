// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-50: which checkpoints the recovery dialog lists and selects, and how it labels them.
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";

const { visibleEntries, defaultSelection, groups } = await load(new URL("../src/recovery-list.ts", import.meta.url).pathname);

const NOW = new Date(2026, 9, 6, 13, 0, 0);
const at = (h, m, s = 0, day = 6) => new Date(2026, 9, day, h, m, s).getTime() / 1000;
const e = (id, epoch, captured, stable, windows = 2, panes = 8) => ({ id, epoch, captured, stable, windows, panes });
const status = (entries, extra = {}) => ({ epoch: "run2", entries, startup: null, job: null, ...extra });

// oldest first, as fbd keeps them
const history = [
  e("a1", "run1", at(8, 0, 0, 5), true),
  e("a2", "run1", at(8, 5, 0, 5), false),        // last before a crash: never stable
  e("b1", "run2", at(12, 0), true),
  e("b2", "run2", at(12, 1), false),              // an intermediate state
  e("b3", "run2", at(12, 2), true),
  e("b4", "run2", at(12, 44, 37), false),         // the newest, not stable yet
];

test("unstable intermediates are hidden; stable, the newest of each run and job/startup sources stay", () => {
  assert.deepEqual(visibleEntries(status(history)).map((x) => x.id), ["a1", "a2", "b1", "b3", "b4"]);
  const withJob = status(history, { job: { id: "j", snapshot: "b2", status: "interrupted" } });
  assert.ok(visibleEntries(withJob).some((x) => x.id === "b2"), "the job's checkpoint is listed");
});

test("opening selects the newest checkpoint, not an older stable one", () => {
  assert.equal(defaultSelection(status(history)), "b4");
});

test("a pending startup selects its source, even an unstable one from a crash", () => {
  const s = status(history, { startup: { snapshot: "a2", phase: "pending" } });
  assert.equal(defaultSelection(s), "a2");
  assert.equal(defaultSelection(status(history, { startup: { snapshot: "a2", phase: "complete" } })), "b4", "done: back to the newest");
});

test("an unfinished restore keeps its checkpoint selected; a complete one does not", () => {
  for (const st of ["running", "interrupted"]) {
    assert.equal(defaultSelection(status(history, { job: { id: "j", snapshot: "b1", status: st } })), "b1", st);
  }
  assert.equal(defaultSelection(status(history, { job: { id: "j", snapshot: "b1", status: "complete" } })), "b4");
});

test("an empty newest checkpoint is skipped", () => {
  assert.equal(defaultSelection(status([...history, e("b5", "run2", at(12, 50), true, 0, 0)])), "b4");
  assert.equal(defaultSelection(status([e("z", "run2", at(12, 0), true, 0, 0)])), "z", "only empty ones: the newest");
  assert.equal(defaultSelection(status([])), "");
});

test("groups: this run first, newest first, readable labels", () => {
  const g = groups(status(history), NOW);
  assert.deepEqual(g.map((x) => x.label), ["This iTerm2 run", "Earlier iTerm2 runs"]);
  assert.deepEqual(g[0].items.map((x) => x.id), ["b4", "b3", "b1"]);
  assert.deepEqual(g[1].items.map((x) => x.id), ["a2", "a1"]);
  assert.match(g[0].items[0].label, /^Today .*12.*44.*37[^·]* · 2 windows, 8 panes · latest$/);
  assert.match(g[1].items[0].label, /^Yesterday .* · 2 windows, 8 panes · last before restart$/);
  assert.doesNotMatch(g[0].items[1].label, /stable|latest|restart/);
  const one = groups(status([e("x", "run2", at(12, 0), true, 1, 1)]), NOW);
  assert.match(one[0].items[0].label, /1 window, 1 pane/);
});

test("without a current epoch the newest checkpoint's run counts as this run", () => {
  const g = groups({ ...status(history), epoch: undefined }, NOW);
  assert.deepEqual(g[0].items.map((x) => x.id), ["b4", "b3", "b1"]);
});
