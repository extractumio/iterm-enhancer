// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
import assert from "node:assert/strict";
import { test } from "node:test";
import { sseData } from "./sse.mjs";

test("fragmented command JSON is delivered exactly once as a complete event", () => {
  const events = [], feed = sseData((s) => events.push(JSON.parse(s)));
  for (const part of ['event: cmd\r\nda', 'ta: {"action":"type', '","text":"é"}\r', '\n', '\n: keepalive\n\ndata: {"action":"which-window"}\n\n']) feed(part);
  assert.deepEqual(events, [{ action: "type", text: "é" }, { action: "which-window" }]);
});

test("data lines join only at an event boundary", () => {
  const events = [], feed = sseData((s) => events.push(s));
  feed("data: first\ndata: second\n");
  assert.deepEqual(events, []);
  feed("\ndata\n\n");
  assert.deepEqual(events, ["first\nsecond", ""]);
});
