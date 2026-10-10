// The web app's output widgets (AC-56): which parts of a terminal's output become code,
// Markdown, a Mermaid diagram or an image button.
import { test } from "node:test";
import assert from "node:assert/strict";
import { imagePaths, logicalLines, regions, scan } from "../../bridge/fbbridge/web/static/outparse.js";

// One row per line, every one with a hard end.
const rows = (text) => text.split("\n").map((t) => ({ text: t, eol: true }));
const found = (text, end) => regions(rows(text), end).map(({ kind, from, to, lang }) => ({ kind, from, to, lang }));

const README = `alex@devbox ~/app % cat README.md
# App

A small tool.

## Install

\`\`\`sh
$ npm install
\`\`\`

- fast
- small
alex@devbox ~/app % `;

test("a file printed by cat is its kind, up to the next prompt (a '$ ' line inside it is not one)", () => {
  assert.deepEqual(found(README), [{ kind: "markdown", from: 1, to: 12, lang: "markdown" }]);
  const py = "alex@devbox ~ % bat -p src/tool.py\nimport os\n\ndef main():\n    return 1\nalex@devbox ~ % ";
  assert.deepEqual(found(py), [{ kind: "code", from: 1, to: 4, lang: "tool.py" }]);
  const mmd = "alex@devbox ~ % cat flow.mmd\ngraph TD\n  A --> B\nalex@devbox ~ % ";
  assert.deepEqual(found(mmd), [{ kind: "mermaid", from: 1, to: 2, lang: "mermaid" }]);
});

test("the next prompt may be the cursor's row, which is not output", () => {
  const text = "alex@devbox ~ % cat a.json\n{\n  \"a\": 1\n}\nalex@devbox ~ % ";
  assert.deepEqual(found(text, 4), [{ kind: "code", from: 1, to: 3, lang: "a.json" }]);
});

test("a fenced block: code with its language, mermaid as a diagram; still open: nothing yet", () => {
  assert.deepEqual(found("Here:\n```python\nprint(1)\n```\ndone"), [{ kind: "code", from: 1, to: 3, lang: "python" }]);
  assert.deepEqual(found("~~~mermaid\nsequenceDiagram\n  A->>B: hi\n~~~"), [{ kind: "mermaid", from: 0, to: 3, lang: "mermaid" }]);
  assert.deepEqual(found("```js\nlet a = 1\n"), []);
  const r = regions(rows("  ```js\n  if (a) {\n    b()\n  }\n  ```"))[0];
  assert.equal(r.text, "if (a) {\n  b()\n}", "the fence's indentation is taken off");
});

test("a Markdown answer: headings, lists and fences in one widget", () => {
  const text = "Sure.\n\n## Plan\n\n1. Read the **config**\n2. Write `out.json`\n\n```sh\nmake\n```\n\nThat is all.\n$ ";
  assert.deepEqual(found(text), [{ kind: "markdown", from: 2, to: 11, lang: "markdown" }]);
});

test("not Markdown: a program's comments, YAML, git log --graph, a lone list", () => {
  const python = "# Load the config\nimport os\nx = os.environ.get('A')\n# Then run\ndef run():\n    return x\n# - not a list";
  const yaml = "# settings\nname: app\nitems:\n  - a\n  - b\n# end";
  const shell = "# install\nmkdir -p \"$dir\"\n# copy\ncp a b && echo ok\n# done\n- item";
  const graph = "* 1a2b3c fix\n* 4d5e6f feat\n* 7a8b9c docs";
  for (const text of [python, yaml, shell, graph]) assert.deepEqual(found(text), [], text);
});

test("a diagram printed without a fence, after a blank line, up to the next blank line", () => {
  assert.deepEqual(found("\nflowchart LR\n  A --> B\n  B --> C\n\nok"), [{ kind: "mermaid", from: 1, to: 3, lang: "mermaid" }]);
  assert.deepEqual(found("draw a graph TD please"), []);
});

test("rows iTerm wrapped are one line; region rows are rows", () => {
  const wrapped = [{ text: "```js", eol: true }, { text: "const long = ", eol: false }, { text: "1;", eol: true }, { text: "```", eol: true }];
  assert.deepEqual(logicalLines(wrapped).map((l) => l.text), ["```js", "const long = 1;", "```"]);
  assert.deepEqual(regions(wrapped).map(({ from, to, text }) => ({ from, to, text })), [{ from: 0, to: 3, text: "const long = 1;" }]);
});

test("image files named in a line", () => {
  assert.deepEqual(imagePaths("Saved /tmp/shot.png and ./b.JPG, not c.txt"), [
    { value: "/tmp/shot.png", start: 6, end: 19 }, { value: "./b.JPG", start: 24, end: 31 }]);
  assert.deepEqual(imagePaths("see https://devbox.example/a.png"), []);
});

test("output a program colored itself is rendered already: bat, glow, a coding agent", () => {
  const styled = rows("```python\nprint(1)\n```").map((r, i) => ({ ...r, styled: i === 1 }));
  assert.deepEqual(regions(styled), []);
});

test("more that is not a widget: GitHub Actions YAML, prose about pies, an encoding line", () => {
  const actions = "name: ci\non: push\n\n# build it\njobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n      - run: make";
  const pie = "\npie chart of the sales\nfor each region";
  const enc = "\n# -*- coding: utf-8 -*-\n\n- a\n- **b**";
  for (const text of [actions, pie, enc]) assert.deepEqual(found(text), [], text);
});

test("settled: where scanning again finds the same regions; an open fence or file waits", () => {
  const done = "```js\na()\n```\n" + "text\n".repeat(20);
  const s = scan(rows(done));
  assert.ok(s.settled > 3 && s.settled <= 24, String(s.settled));
  // the last lines always wait (a document may go on); an open fence or file further back too
  assert.equal(scan(rows("x\n".repeat(40) + "```js\n" + "a()\n".repeat(30))).settled, 40, "an open fence");
  assert.equal(scan(rows("x\n".repeat(40) + "alex@devbox ~ % cat a.py\n" + "print(1)\n".repeat(30))).settled, 40, "a file whose prompt has not come back");
  const tail = rows(done + "## Next\n\n- a\n- b");
  const all = regions(tail), from = scan(tail).settled;
  assert.deepEqual(regions(tail.slice(from)).map((r) => r.from + from), all.filter((r) => r.from >= from).map((r) => r.from));
});

test("10000 lines of a list take little time (no rescan per line)", () => {
  const text = Array.from({ length: 10000 }, (_, i) => `- item ${i}`).join("\n");
  const t0 = performance.now();
  assert.deepEqual(found(text), []);
  assert.ok(performance.now() - t0 < 500, `${performance.now() - t0} ms`);
});

test("settled never stays far behind: a log with a list item on every line, a file whose prompt never came back", () => {
  const log = rows(Array.from({ length: 3000 }, (_, i) => `- step ${i} done`).join("\n"));
  assert.ok(scan(log).settled >= 3000 - 2000, String(scan(log).settled));
  const tail = rows("alex@devbox ~ % tail -f app.log\n" + Array.from({ length: 3000 }, (_, i) => `line ${i}`).join("\n"));
  assert.ok(scan(tail).settled >= 3001 - 2000, String(scan(tail).settled));
});

test("Starship's prompt: a colored line, then a clock and ❯; the next one a minute later", () => {
  const lines = [["", false], ["~/app via 🦀 v1.98", true], ["at 12:21 ❯ cat guide.md", false], ["# Guide", false], ["", false],
    ["- one", false], ["- **two**", false], ["", false], ["~/app via 🦀 v1.98", true], ["at 12:22 ❯ cat flow.mmd", false],
    ["flowchart LR", false], ["  A --> B", false], ["", false], ["~/app via 🦀 v1.98", true], ["at 12:22 ❯ sh answer.sh", false],
    ["## Plan", false], ["", false], ["1. Read the **config**", false], ["2. Run `make`", false], ["", false], ["That is all.", false],
    ["", false], ["~/app via 🦀 v1.98", true]].map(([text, styled]) => ({ text, eol: true, styled }));
  lines.splice(15, 0, { text: "", eol: true, styled: false });   // a blank line before the heading
  const got = regions(lines, lines.length).map(({ kind, from, to }) => ({ kind, from, to }));
  assert.deepEqual(got, [{ kind: "markdown", from: 3, to: 6 }, { kind: "mermaid", from: 10, to: 11 }, { kind: "markdown", from: 16, to: 21 }]);
});
