// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The latency trace (AC-55) records what kind of key was sent, never what it says.
import { test } from "node:test";
import assert from "node:assert/strict";
import { keyKind, keyFields } from "../../bridge/fbbridge/web/static/trace.js";

test("keys by kind", () => {
  const kinds = { "a": "char", "я": "char", "😀": "char", "ls -la": "text", "\r": "enter", "\x7f": "backspace", "\t": "tab",
    "\x1b": "esc", "\x1b[A": "seq", "\x1b[200~x\x1b[201~": "seq", "\x03": "ctrl", "": "none" };
  for (const [data, kind] of Object.entries(kinds)) assert.equal(keyKind(data), kind, JSON.stringify(data));
});

test("without ?trace=1 a key gets no number", () => {
  assert.deepEqual(keyFields("a", "keydown", null), {});
});
