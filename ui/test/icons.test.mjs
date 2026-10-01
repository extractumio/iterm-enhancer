// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-32: every file name maps to an icon that exists, as the spec's examples say.
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";

const { ICONS, iconFor, fileIcon, folderIcon, copyIcon } = await load("src/icons.ts");

test("every icon is a Tabler SVG", () => {
  for (const [name, svg] of Object.entries(ICONS)) assert.match(svg, /^<svg[\s\S]*<\/svg>\s*$/, name);
});

test("spec examples (AC-32)", () => {
  const cases = {
    "main.rs": ["code", "code"], "app.py": ["code", "code"], "package.json": ["braces", "conf"],
    "Cargo.toml": ["settings", "conf"], ".env": ["settings", "conf"], "README.md": ["markdown", "doc"],
    "notes.txt": ["text", "doc"], "logo.png": ["photo", "img"], "a.svg": ["photo", "img"],
    "report.pdf": ["pdf", "img"], "dist.tar.gz": ["zip", "arch"], "index.html": ["web", "web"],
    "a.css": ["web", "web"], "build.sh": ["terminal", "code"], "Cargo.lock": ["lock", "conf"],
    ".gitignore": ["git", "conf"], "id.pem": ["key", "conf"], "tls.key": ["key", "conf"],
    "data.csv": ["table", "doc"], "db.sqlite": ["database", "code"], "q.sql": ["database", "code"],
    "song.mp3": ["music", "img"], "clip.mp4": ["movie", "img"], "font.woff2": ["font", "doc"],
    "a.out": ["binary", "faint"], "lib.dylib": ["binary", "faint"], "LICENSE": ["file", "faint"],
    "Makefile": ["settings", "conf"], "Dockerfile": ["settings", "conf"], "id_ed25519": ["key", "conf"],
    "package-lock.json": ["lock", "conf"], ".env.local": ["settings", "conf"],
  };
  for (const [name, [icon, color]] of Object.entries(cases)) assert.deepEqual(iconFor(name), { icon, color }, name);
});

test("row icons carry the size class and a theme color; strings are reused", () => {
  const a = fileIcon("main.rs");
  assert.match(a, /^<svg class="ico" style="color:var\(--k-code\)"/);
  assert.doesNotMatch(a, /M0 0h24v24H0z/, "Tabler's invisible frame path is dropped");
  assert.equal(fileIcon("lib.rs"), a);
  assert.match(folderIcon(true), /color:var\(--folder\)/);
  assert.notEqual(folderIcon(true), folderIcon(false));
  assert.match(copyIcon, /^<svg width="14" height="14"/);
});
