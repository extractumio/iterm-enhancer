// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-04 / OQ-05: Markdown rendering is safe and resolves local resources.
// Bundles src/markdown.ts for node (highlighting is DOM-only and not exercised here).
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";

const { mdToHtml } = await load("src/markdown.ts", { define: { "location.search": '"?t=tok123"' } });

const P = "/Users/alex/work/api/README.md";

test("tables, task lists, fenced code", () => {
  const h = mdToHtml("| a | b |\n|---|---|\n| 1 | 2 |\n\n- [x] done\n- [ ] todo\n\n```rust\nfn main() {}\n```\n", P);
  assert.match(h, /<table>[\s\S]*<td>1<\/td>/);
  assert.match(h, /checked="" disabled="" type="checkbox"> done/);
  assert.match(h, /<pre><code class="language-rust">fn main\(\) \{\}/);
});

test("raw HTML is escaped, not executed", () => {
  const h = mdToHtml('<img src=x onerror=alert(1)>\n\n<script>alert(2)</script>', P);
  assert.ok(!h.includes("<img src=x"), h);
  assert.ok(!h.includes("<script>"), h);
  assert.match(h, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("javascript: links are dropped", () => {
  const h = mdToHtml("[x](javascript:alert(1))", P);
  assert.ok(!/href="javascript:/i.test(h), h);
});

test("relative images go through /api/raw with an absolute path", () => {
  const h = mdToHtml("![arch](docs/arch.png)", P);
  assert.match(h, /src="\/api\/raw\?path=%2FUsers%2Falex%2Fwork%2Fapi%2Fdocs%2Farch\.png&amp;t=tok123"/);
});

test("remote images are not loaded", () => {
  const h = mdToHtml("![logo](https://example.com/logo.png)", P);
  assert.ok(!h.includes("<img"), h);
  assert.match(h, /class="remote-img"/);
});

test("headings get anchors", () => {
  assert.match(mdToHtml("## Install & Run", P), /<h2 id="install--run">/);
});
