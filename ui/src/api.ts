// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Thin client for the fbd JSON API. The token comes from the tool URL (?t=…).

/** This web view's storage, which survives in-page reloads; private mode may refuse it. */
export const session = {
  get: (k: string) => { try { return sessionStorage.getItem(k); } catch { return null; } },
  set: (k: string, v: string | null) => {
    try { if (v == null) sessionStorage.removeItem(k); else sessionStorage.setItem(k, v); } catch { /* private mode */ }
  },
};

/** The token arrives in the tool URL once; it is kept for this web view only (reloads keep
 *  working) and removed from the address, so no document can see it in a URL or Referer. */
export let TOKEN = new URLSearchParams(location.search).get("t") ?? session.get("fb.token") ?? "";
export function keepToken() {
  if (TOKEN) session.set("fb.token", TOKEN);
  const u = new URL(location.href);
  if (u.searchParams.has("t") || u.searchParams.has("v")) {
    u.searchParams.delete("t"); u.searchParams.delete("v");
    history.replaceState(null, "", u.pathname + (u.search || ""));
  }
}

/** A viewer window's URL carries a one-time code instead of the token: trade it in once. */
export async function redeemTicket(code: string) {
  const res = await fetch(`/ticket?v=${encodeURIComponent(code)}`);
  const body = await res.json().catch(() => null);
  if (!res.ok || !body?.token) throw new ApiError(res.status, body?.error ?? "bad_ticket", body?.message ?? "Viewer link expired", body);
  TOKEN = body.token;
}
/** Names this panel in workspace events, so it can ignore the echo of its own writes. */
export const CLIENT = Math.random().toString(36).slice(2, 10);

export interface Row { n: string; k: "d" | "f" | "l" | "L"; s?: number }
export interface Page {
  status: "ready" | "loading" | "error"; total: number; writable: boolean;
  gen: number; entries: Row[]; error?: string; located?: number;
}
/** A changed file and its etag now (null: gone), as sent in fs-change events. */
export interface Stamp { path: string; etag: string | null }
export interface FsChange { dirs: string[]; files: Stamp[]; moved: { from: string; to: string }[]; host?: string }
export interface Tab { path: string; view: "auto" | "rendered" | "source" }
export interface Pane {
  rev: number; root: string; expanded: string[]; selected: string[]; scroll: number;
  tabs: Tab[]; active_tab: number | null; updated: number;
}
export interface Theme {
  bg?: string; fg?: string; sel?: string; selfg?: string; cursor?: string; link?: string;
  ansi?: string[]; font?: string | null; size?: number | null; dark?: boolean;
}
export interface TermState {
  version: number; bridge: boolean; key?: string; session?: string; title?: string;
  mode?: string; note?: string; job?: string; busy?: boolean; cwd?: string | null; stale?: boolean; theme?: Theme;
  window?: string;  // the iTerm2 window of the pane (AC-36)
  panel?: boolean;  // false: that window shows no Toolbelt, so no panel lives there
  build?: string;   // fbd's build: another one than this page's means an upgrade (AC-34)
  host?: string;    // a remote pane's host: its files are reached through that host's agent (AC-37)
  /** a remote pane's host as offered to the user (AC-38): key = its ssh arguments */
  remote?: { key: string; name: string; state: "ask" | "dismissed" | "enabling" | "connecting" | "up" | "down" };
}
export interface FileView {
  path: string; size: number; etag: string; binary: boolean; mime: string | null;
  truncated: boolean; utf8: boolean; writable: boolean; text?: string;
}
export interface Prefs { hidden: boolean; split: number }
export const DEFAULT_PREFS: Prefs = { hidden: true, split: 0.5 };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public body: any) { super(message); }
}

type Query = Record<string, string | number | boolean | undefined>;

export async function api<T>(method: string, path: string, opts: { query?: Query; body?: unknown; headers?: Record<string, string>; retry?: boolean } = {}): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined) qs.set(k, String(v));
  const url = path + (qs.size ? `?${qs}` : "");
  const headers: Record<string, string> = { "X-FB-Token": TOKEN, "X-FB-Client": CLIENT, ...(scope ? { "X-FB-Host": scope } : {}), ...opts.headers };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  for (let waited = 0, step = 250; ; waited += step, step *= 2) {
    try {
      res = await fetch(url, { method, headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) });
      break;
    } catch {
      // reads ride out a backend restart (AC-34); writes fail at once, never repeated behind the user
      if (method !== "GET" || opts.retry === false || waited >= 3000) throw new ApiError(0, "network", "Backend not reachable (restarting?)", null);
      await new Promise((r) => setTimeout(r, step));
    }
  }
  if (res.status === 204 || res.status === 304) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body?.error ?? "http", body?.message ?? `HTTP ${res.status}`, body);
  return body as T;
}

/** The host whose files the panel shows (a remote pane, AC-37), or null for this Mac. Every
 *  file request names it; fbd sends such a request to that host's agent and nowhere else. */
export let scope: string | null = null;
export const setScope = (host: string | null) => { scope = host || null; };

export const rawUrl = (path: string) =>
  `/api/raw?path=${encodeURIComponent(path)}&t=${encodeURIComponent(TOKEN)}${scope ? `&host=${encodeURIComponent(scope)}` : ""}`;
export const eventsUrl = () => `/api/events?t=${encodeURIComponent(TOKEN)}&client=${CLIENT}`;

/** Run an API call; on failure show its message and resolve to undefined. */
export const apiOrToast = <T>(...args: Parameters<typeof api<T>>) => api<T>(...args).catch((e: Error) => { toast(e.message); return undefined; });

export const isDirKind = (k: Row["k"]) => k === "d" || k === "L";
export * from "./paths";
export function fmtSize(n?: number | null): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} K`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} M`;
  return `${(n / 1073741824).toFixed(1)} G`;
}

export const esc = (s: string) =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

let toastTimer = 0;
export function toast(msg: string) {
  const el = document.getElementById("toast")!;
  el.textContent = msg;
  el.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.remove("on"), 3500);
}
