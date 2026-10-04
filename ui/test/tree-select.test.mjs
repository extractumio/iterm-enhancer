// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";
const { selectRange } = await load("src/tree-select.ts");

test("range selection awaits every missing page and includes every entry", async () => {
  const selected = [], pages = [], loaded = new Set();
  let pending = 0, peak = 0;
  const done = await selectRange(0, 1204, async (i) => {
    const page = Math.floor(i / 500);
    if (!loaded.has(page)) {
      peak = Math.max(peak, ++pending);
      await new Promise((r) => setTimeout(r, 0));
      pages.push(page); loaded.add(page); pending--;
    }
    return `/sandbox/file-${i}`;
  }, () => true, (p) => selected.push(p));
  assert.equal(done, true);
  assert.deepEqual(pages, [0, 1, 2]);
  assert.equal(peak, 1);
  assert.equal(selected.length, 1205);
  assert.equal(selected.at(-1), "/sandbox/file-1204");
});

test("another input cancels a large range at a paint/input yield", async () => {
  let alive = true, count = 0;
  setTimeout(() => { alive = false; }, 0);
  assert.equal(await selectRange(0, 499999, async (i) => `/sandbox/${i}`, () => alive, () => count++), false);
  assert.equal(count, 500);
});
