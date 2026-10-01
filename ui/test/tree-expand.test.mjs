// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-31: "expand all" decides breadth-first with limits, and stops when cancelled.
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";

const { expandAll, summary, MAX_DEPTH, MAX_FOLDERS } = await load("src/tree-expand.ts");

const ROOT = "/Users/alex/proj";
/** A fake disk: path → entries ("name/" is a folder, "name@" a symlinked folder). */
function ops(disk, { cancelAfter = Infinity, unreadable = [] } = {}) {
  const expanded = [], collapsed = [];
  let calls = 0;
  return {
    expanded, collapsed,
    alive: () => calls < cancelAfter,
    async expand(p) {
      calls++;
      if (unreadable.includes(p)) return null;
      const names = disk[p] ?? [];
      if (p !== ROOT) expanded.push(p);
      const rows = (Array.isArray(names) ? names : []).map((n) => n.endsWith("/") ? { n: n.slice(0, -1), k: "d" } : n.endsWith("@") ? { n: n.slice(0, -1), k: "L" } : { n, k: "f" });
      return { total: typeof names === "number" ? names : rows.length, rows };
    },
    collapse(p) { collapsed.push(p); },
  };
}

test("expands real folders breadth-first and skips heavy ones", async () => {
  const o = ops({
    [ROOT]: ["src/", "docs/", "node_modules/", ".git/", "link@", "README.md"],
    [`${ROOT}/src`]: ["a/", "main.rs"], [`${ROOT}/src/a`]: ["b/"], [`${ROOT}/src/a/b`]: [], [`${ROOT}/docs`]: ["x.md"],
  });
  const r = await expandAll(ROOT, ROOT, o);
  assert.deepEqual(o.expanded, [`${ROOT}/src`, `${ROOT}/docs`, `${ROOT}/src/a`, `${ROOT}/src/a/b`]);
  assert.equal(summary(r), "Expanded 4 folders · skipped 2 (node_modules, .git)");
});

test("a chosen folder is expanded itself; large folders collapse again", async () => {
  const o = ops({ [`${ROOT}/src`]: ["big/", "small/"], [`${ROOT}/src/big`]: 501, [`${ROOT}/src/small`]: [] });
  const r = await expandAll(`${ROOT}/src`, ROOT, o);
  assert.deepEqual(o.collapsed, [`${ROOT}/src/big`]);
  assert.equal(r.expanded, 2);
  assert.equal(summary(r), "Expanded 2 folders · skipped 1 (1 too large)");
});

test("depth and folder limits", async () => {
  const deep = { [ROOT]: ["d/"] };
  let p = ROOT;
  for (let i = 0; i < 12; i++) { p += "/d"; deep[p] = ["d/"]; }
  const r = await expandAll(ROOT, ROOT, ops(deep));
  assert.equal(r.expanded, MAX_DEPTH);
  assert.ok(r.depthLimit && summary(r).includes(`depth limit ${MAX_DEPTH}`));

  const wide = { [ROOT]: Array.from({ length: 300 }, (_, i) => `f${i}/`) };
  const w = await expandAll(ROOT, ROOT, ops(wide));
  assert.equal(w.expanded, MAX_FOLDERS);
  assert.ok(w.folderLimit && summary(w).endsWith(`limit ${MAX_FOLDERS} folders`));
});

test("cancelled: stops and reports nothing to persist", async () => {
  const o = ops({ [ROOT]: Array.from({ length: 20 }, (_, i) => `f${i}/`) }, { cancelAfter: 3 });
  const r = await expandAll(ROOT, ROOT, o);
  assert.ok(r.cancelled);
  assert.ok(o.expanded.length < 20);
});

test("an unreadable folder does not stop the others", async () => {
  const o = ops({ [ROOT]: ["secret/", "ok/"], [`${ROOT}/ok`]: [] }, { unreadable: [`${ROOT}/secret`] });
  const r = await expandAll(ROOT, ROOT, o);
  assert.deepEqual(o.expanded, [`${ROOT}/ok`]);
  assert.equal(r.expanded, 1);
});
