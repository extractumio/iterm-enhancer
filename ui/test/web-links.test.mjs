// The web app's links in terminal text (AC-52): which address or path a click is on.
import { test } from "node:test";
import assert from "node:assert/strict";
import { absolutePath, tokenAt, tokens } from "../../bridge/fbbridge/web/static/links.js";

const at = (text, word) => tokenAt(text, text.indexOf(word) + 1);

test("a web address, without the sentence's punctuation", () => {
  const line = "I've published it: https://claude.ai/artifact/WhVgpFjvZJFLxSav4uaGrj.";
  assert.deepEqual(at(line, "claude.ai"), { kind: "url", value: "https://claude.ai/artifact/WhVgpFjvZJFLxSav4uaGrj" });
  assert.deepEqual(at("(see http://devbox.example:8080/a?b=1)", "devbox"), { kind: "url", value: "http://devbox.example:8080/a?b=1" });
  assert.equal(at(line, "published"), null);
});

test("file paths and names, with a line number", () => {
  assert.deepEqual(at("Artifact(\"patchstack-connect-vs-rasp.html\")", "patchstack"), { kind: "path", value: "patchstack-connect-vs-rasp.html" });
  assert.deepEqual(at("edit src/db/pool.rs:42:7 now", "pool"), { kind: "path", value: "src/db/pool.rs", line: 42 });
  assert.deepEqual(at("cd ~/nodejs-rasp", "nodejs"), { kind: "path", value: "~/nodejs-rasp" });
  assert.deepEqual(at("see ../README.md,", "README"), { kind: "path", value: "../README.md" });
  assert.deepEqual(at("a Makefile here", "Makefile"), { kind: "path", value: "Makefile" });
  assert.deepEqual(at("/etc/hosts", "etc"), { kind: "path", value: "/etc/hosts" });
});

test("prose, numbers and versions are not paths", () => {
  for (const [text, word] of [["read and/or write", "and"], ["on 10/07/2026", "07"], ["version 1.2.3", "1.2"],
    ["the end.", "end"], ["e.g. this", "e.g"], ["foo.bar", "foo"], ["just /", "/"]]) {
    assert.equal(at(text, word), null, text);
  }
});

test("a printed path as an absolute one, from the pane's folder and home", () => {
  assert.equal(absolutePath("src/a.rs", "/Users/alex/p", "/Users/alex"), "/Users/alex/p/src/a.rs");
  assert.equal(absolutePath("../b/./c.md", "/Users/alex/p", null), "/Users/alex/b/c.md");
  assert.equal(absolutePath("~/notes.md", "/x", "/home/alex"), "/home/alex/notes.md");
  assert.equal(absolutePath("/etc/../etc/hosts", null, null), "/etc/hosts");
  assert.equal(absolutePath("~/notes.md", "/x", null), null);
  assert.equal(absolutePath("a.md", null, "/h"), null);
});

test("every address and path of a text, where each is named: what is underlined is what opens", () => {
  const text = "Edit src/db/pool.rs:42, then see https://devbox.example/a. Or ~/notes.md and/or 1.2.3";
  const found = tokens(text);
  assert.deepEqual(found.map((t) => text.slice(t.start, t.end)), ["src/db/pool.rs:42", "https://devbox.example/a", "~/notes.md"]);
  for (const t of found) assert.deepEqual(tokenAt(text, t.start), { kind: t.kind, value: t.value, ...(t.line ? { line: t.line } : {}) });
  assert.equal(tokenAt(text, text.indexOf("and/or")), null);
});
