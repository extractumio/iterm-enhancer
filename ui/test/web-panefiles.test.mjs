// The web app's check of the files a pane's output names (AC-52, AC-56): one listing per folder,
// answers per place (a "cd" moves relative names), looked for again later, a pane's folder not
// known said, another pane's answers dropped.
import { test } from "node:test";
import assert from "node:assert/strict";
import { paneFiles } from "../../bridge/fbbridge/web/static/panefiles.js";

let now = 0;
performance.now = () => now;                       // the module's clock

const tick = (ms = 300) => new Promise((r) => setTimeout(r, ms));   // answers, then the 250 ms "turned up"

/** A pane in `cwd` and a fake fbd listing `folders` ({dir: [names]}); `big`: folders past a page. */
function fake(folders, { cwd = "/Users/alex/p", big = new Set(), info = null } = {}) {
  const asked = [], pane = { cwd, home: "/Users/alex", host: null };
  const files = paneFiles(async () => info ?? { ...pane }, async (url) => {
    const q = new URL(url, "http://x").searchParams;
    asked.push(Object.fromEntries(q));
    const names = folders[q.get("path")] ?? [];
    const f = q.get("filter");
    const entries = names.filter((n) => !f || n.includes(f)).slice(0, big.has(q.get("path")) && !f ? 2 : 500).map((n) => ({ n, k: n.endsWith("/") ? "d" : "f" }));
    return { status: "ready", total: f ? entries.length : names.length, entries };
  });
  return { files, asked, pane };
}

test("every name of a folder is answered by one listing; listeners hear once", async () => {
  const names = Array.from({ length: 200 }, (_, i) => `f${i}.js`);
  const { files, asked } = fake({ "/Users/alex/p": names });
  let heard = 0;
  files.listen(() => heard++);
  files.there("f0.js");
  await tick();                                    // the pane's folder came
  for (const n of [...names, "gone.js"]) files.there(n);
  await tick();
  assert.equal(asked.length, 1);
  assert.ok(names.every((n) => files.there(n)));
  assert.equal(files.there("gone.js"), false);
  assert.ok(heard >= 1 && heard <= 3, String(heard));
});

test("a name printed before a cd is taken from the folder now", async () => {
  const { files, pane } = fake({ "/Users/alex/a": ["marks.js"], "/Users/alex/b": [] }, { cwd: "/Users/alex/a" });
  files.there("marks.js"); await tick(); files.there("marks.js"); await tick();
  assert.equal(files.there("marks.js"), true);
  pane.cwd = "/Users/alex/b";
  now += 6000;                                     // the pane's folder is asked again
  files.there("marks.js"); await tick(); files.there("marks.js"); await tick();
  assert.equal(files.there("marks.js"), false);
});

test("a file not there is looked for again later, then less often; a folder is not a file", async () => {
  const folders = { "/Users/alex/p": ["sub/"] };
  const { files, asked } = fake(folders);
  files.there("new.md"); await tick(); files.there("new.md"); files.there("sub"); await tick();
  assert.equal(files.there("sub"), false, "a folder");
  const n = asked.length;
  folders["/Users/alex/p"].push("new.md");
  now += 5000; files.there("new.md"); await tick();
  assert.equal(asked.length, n, "not again within 10 s");
  now += 6000; files.there("new.md"); await tick();
  assert.equal(files.there("new.md"), true);
});

test("a folder larger than a page is asked by name", async () => {
  const { files, asked } = fake({ "/Users/alex/p": ["a.rs", "b.rs", "c.rs", "x.rs"] }, { big: new Set(["/Users/alex/p"]) });
  files.there("x.rs"); await tick(); files.there("x.rs"); await tick();
  assert.equal(files.there("x.rs"), true);
  assert.ok(asked.some((q) => q.filter === "x.rs"));
});

test("a pane whose folder is not known says why; another pane's answers are dropped", async () => {
  const off = fake({}, { info: { error: "This session's folder is not known yet (tmux)." } });
  off.files.state("a.md"); await tick();
  assert.deepEqual(off.files.state("a.md"), { error: "This session's folder is not known yet (tmux)." });
  const { files } = fake({ "/Users/alex/p": ["a.md"] });
  files.there("a.md"); await tick(); files.there("a.md");
  files.forget();                                  // the listing is on its way
  await tick();
  assert.equal(files.there("a.md"), false);
});
