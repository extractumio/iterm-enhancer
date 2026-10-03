// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-41 checks for e2e_panel.mjs: many panels at once each load and follow, because events
// come over a WebSocket (Chromium, like WebKit, keeps 6 HTTP connections per host), and
// fbd refuses a socket from another origin or without the token.

import http from "node:http";
import { base, check, onSocket, PORT, push, SB, TOKEN, within } from "./e2e_harness.mjs";

const PANELS = 12;
const health = async () => JSON.parse((await onSocket("GET", "/health")).text);

/** The status fbd answers a WebSocket upgrade with. */
const upgrade = (query, headers) => new Promise((resolve) => {
  const req = http.get({ host: "127.0.0.1", port: PORT, path: `/api/ws${query}`, headers: {
    Connection: "Upgrade", Upgrade: "websocket", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", ...headers,
  } });
  req.on("upgrade", (res, socket) => { socket.destroy(); resolve(res.statusCode); });
  req.on("response", (res) => { res.resume(); resolve(res.statusCode); });
  req.on("error", () => resolve(0));
});

export async function manyPanels(browser, row) {
  const origin = `http://127.0.0.1:${PORT}`;
  check("AC-41 the panel's origin with the token opens the socket", await upgrade(`?t=${TOKEN}`, { Origin: origin }) === 101);
  check("AC-41 another origin is refused", await upgrade(`?t=${TOKEN}`, { Origin: "http://evil.example" }) === 403);
  check("AC-41 no origin is refused", await upgrade(`?t=${TOKEN}`, {}) === 403);
  check("AC-41 no token is refused", await upgrade("", { Origin: origin }) === 401);

  await push("e2eA", SB);
  const before = (await health()).ws_clients;
  const ctx = await browser.newContext({ viewport: { width: 400, height: 500 } });
  const pages = await Promise.all(Array.from({ length: PANELS }, () => ctx.newPage()));
  await within(`AC-41 ${PANELS} panels at once all load their tree`, async (ms) => {
    await Promise.all(pages.map(async (p) => {
      await p.goto(`${base}/?t=${TOKEN}`, { timeout: ms });
      await p.waitForSelector(row("src"), { timeout: ms });
    }));
  }, 15000);
  const h = await health();
  check(`AC-41 … over ${PANELS} sockets, no SSE stream`, h.ws_clients === before + PANELS && h.sse_clients === 0, JSON.stringify({ ws: h.ws_clients, sse: h.sse_clients }));
  await push("e2eB", `${SB}/src`);
  await within("AC-41 … and every one follows the next pane", async (ms) => {
    await Promise.all(pages.map((p) => p.waitForSelector(row("src/a"), { timeout: ms })));
  });
  await push("e2eA", SB);
  await ctx.close();
}
