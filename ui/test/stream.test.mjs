// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-41: events arrive over one WebSocket; a close brings it back after a new proof, and a
// proof that returns late never opens a second socket nor one that was closed meanwhile.
import assert from "node:assert/strict";
import { test } from "node:test";
import { load } from "./bundle.mjs";

// the proof, resolved by the test (pending proofs in globalThis.proofs); api.ts needs a browser
const API = `
  export class ApiError extends Error {}
  export const proveServer = () => new Promise((resolve) => globalThis.proofs.push(resolve));
  export const forgetProof = () => {};
  export const socketUrl = () => "ws://127.0.0.1:47821/api/ws";
`;
const stubApi = {
  name: "stub-api",
  setup(b) {
    b.onResolve({ filter: /^\.\/api$/ }, () => ({ path: "api", namespace: "stub" }));
    b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({ contents: API, loader: "js" }));
  },
};

globalThis.proofs = [];
const timers = [];
const sockets = [];
globalThis.window = { setTimeout: (fn, ms) => timers.push({ fn, ms }) };
globalThis.clearTimeout = () => {};
// a browser fires onclose later than close(); firing it at once is enough for these checks
globalThis.WebSocket = class {
  constructor(url) { this.url = url; this.closed = false; sockets.push(this); }
  close() { this.closed = true; this.onclose?.(); }
};
const { Stream } = await load("src/stream.ts", { plugins: [stubApi] });

const open = () => sockets.filter((s) => !s.closed).length;
const flush = () => new Promise((r) => setImmediate(r));
const prove = async () => { globalThis.proofs.shift()(); await flush(); };

function fresh() {
  sockets.length = timers.length = globalThis.proofs.length = 0;
  const seen = { open: 0, lost: 0, events: [] };
  const s = new Stream({ on: { state: (d) => seen.events.push(d) }, open: () => seen.open++, lost: () => seen.lost++ });
  return { s, seen };
}

test("events arrive by name with their data", async () => {
  const { s, seen } = fresh();
  void s.connect(); await prove();
  sockets[0].onopen();
  sockets[0].onmessage({ data: JSON.stringify({ event: "state", data: { cwd: "/Users/alex" } }) });
  sockets[0].onmessage({ data: JSON.stringify({ event: "unknown", data: 1 }) });
  assert.equal(seen.open, 1);
  assert.deepEqual(seen.events, [{ cwd: "/Users/alex" }]);
  assert.match(sockets[0].url, /^ws:/);
});

test("closed by fbd (a restart, or the panel fell behind): opened again after a new proof", async () => {
  const { s, seen } = fresh();
  void s.connect(); await prove();
  sockets[0].closed = true; sockets[0].onclose();
  assert.equal(seen.lost, 1);
  timers.at(-1).fn(); await prove();
  assert.equal(sockets.length, 2);
  assert.equal(open(), 1);
});

test("a second connect while the first proof is pending: one socket, not two", async () => {
  const { s } = fresh();
  void s.connect();
  void s.connect();
  for (const resolve of globalThis.proofs.splice(0)) resolve();
  await flush();
  assert.equal(sockets.length, 1);
});

test("closed while the proof is pending: nothing opens", async () => {
  const { s } = fresh();
  void s.connect();
  s.close();
  await prove();
  assert.equal(sockets.length, 0);
});

test("closed by the panel: no reconnect", async () => {
  const { s, seen } = fresh();
  void s.connect(); await prove();
  s.close();
  assert.equal(open(), 0);
  assert.equal(seen.lost, 0);
  assert.equal(timers.length, 0);
});
