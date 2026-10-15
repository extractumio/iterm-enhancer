// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Reading a context trace (AC-55, ?trace=2, docs/web-trace.md): its lines, and its rows back
// into the bridge's form for a check that plays it again.
import { test } from "node:test";
import assert from "node:assert/strict";
import { device, histLines, parseTrace, rowData, screenMessage, summary } from "./tracereplay.mjs";
import { fit } from "../../bridge/fbbridge/web/static/tracebuf.js";

const TRACE = `#ite-trace 1 {"at":"2026-10-14T10:00:00.000Z","up":900,"events":4,"dropped":2,"doc":"docs/web-trace.md"}
100 key {"p":"keydown","d":"a","in":0.4}
160 echo {"keys":1,"rt":30,"draw":2,"paint":14,"total":46.4,"ch":1,"cur":[1,3]}
300 box {"top":0,"bottom":2,"end":2,"caret":[1,3],"rows":3}
|0 er|──────
|1 e 2+1*256|❯ a
|2 er|──────
300 web {"at":[8,600,374,20],"lines":1,"cur":[0,3]}
:0 11|❯ a\x20
800 err {"msg":"boom","at":"app.js:1:2","stack":""}
#state
900 env {"touch":true}
900 size {"win":[390,760],"dpr":3}
900 tail {"rows":1}
|-1 e|$ ls
900 screen {"cols":40,"rows":2,"top":5,"alt":false,"box":null}
|0 -|hello world
|1 e 0+2*4097,4+1*256|$ ls
`;

test("a trace's lines: header, events with their rows, the state", () => {
  const t = parseTrace(TRACE);
  assert.equal(t.head.dropped, 2);
  assert.deepEqual(t.events.map((e) => e.ev), ["key", "echo", "box", "web", "err"]);
  assert.deepEqual(t.events[2].rows.map((r) => [r.i, r.flags, r.text]), [[0, "er", "──────"], [1, "e", "❯ a"], [2, "er", "──────"]]);
  assert.deepEqual(t.events[3].rows, [{ k: 0, x: 11, text: "❯ a " }]);
  assert.deepEqual(t.state.map((e) => e.ev), ["env", "size", "tail", "screen"]);
});

test("rows back into the bridge's form: runs, the cursor past the text, a background, no hard end", () => {
  assert.deepEqual(rowData({ flags: "e", runs: [{ at: 2, n: 1, f: 256 }], text: "❯ a" }), { r: [["❯ ", null, null, 0], ["a", null, null, 256]], e: true });
  assert.deepEqual(rowData({ flags: "e", runs: [{ at: 3, n: 1, f: 256 }], text: "$ a" }), { r: [["$ a", null, null, 0], [" ", null, null, 256]], e: true });
  assert.deepEqual(rowData({ flags: "", runs: [], text: "x" }), { r: [["x", null, null, 0]], e: false });
  const t = parseTrace(TRACE), m = screenMessage(t);
  assert.equal(m.cols, 40);
  assert.deepEqual(m.ch[1], [1, { r: [["$ ", null, "#444", 1], ["ls", null, null, 0], [" ", null, null, 256]], e: true }]);
  assert.deepEqual(histLines(t), [{ r: [["$ ls", null, null, 0]], e: true }]);
  assert.deepEqual(device(t), { viewport: { width: 390, height: 760 }, deviceScaleFactor: 3, hasTouch: true, isMobile: true });
});

test("the summary names the echo's stages and what went wrong", () => {
  const s = summary(parseTrace(TRACE));
  assert.match(s, /echo\.total n 1 p50 46\.4/);
  assert.match(s, /err at 800: \{"msg":"boom"/);
});

test("a long event stays JSON within its limit: its texts shortened first, then its last fields", () => {
  const stack = Array.from({ length: 8 }, (_, i) => `    at handler${i} (app.js:${100 + i}:17)`).join("\n");
  const j = fit({ msg: "x".repeat(200), at: "app.js:1:2", stack }, 380);
  const o = JSON.parse(j);
  assert.ok(j.length <= 380, j.length);
  assert.equal(o.cut, true);
  assert.equal(o.at, "app.js:1:2");
  assert.match(o.stack, /^    at handler0/);
  assert.equal(fit({ a: 1 }, 100), '{"a":1}');
  assert.ok(JSON.parse(fit(Object.fromEntries(Array.from({ length: 60 }, (_, i) => [`k${i}`, i])), 120)).cut);
});

test("a line that is not of the format is kept and named in the summary", () => {
  const t = parseTrace(`${TRACE}12 err {"msg":"cut…\n`);
  assert.equal(t.state.at(-1).ev, "raw");
  assert.match(summary(t), /unreadable line: 12 err/);
});
