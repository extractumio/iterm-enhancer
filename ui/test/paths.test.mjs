// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";
const { isUnder, decodePath } = await load("src/paths.ts");

test("filesystem root includes descendants without confusing sibling prefixes", () => {
  assert.equal(isUnder("/tmp/project", "/"), true);
  assert.equal(isUnder("/", "/"), true);
  assert.equal(isUnder("/tmp/project/file", "/tmp/project"), true);
  assert.equal(isUnder("/tmp/project2", "/tmp/project"), false);
});

test("literal percent filenames remain usable and encoded separators decode", () => {
  assert.equal(decodePath("100%.png"), "100%.png");
  assert.equal(decodePath("a%23b%3Fc.png"), "a#b?c.png");
});
