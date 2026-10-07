// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Files panel: follows the focused terminal pane (pushed by fbd over SSE), shows its
// directory as a virtual tree, opens files in tabs, edits and saves them, and keeps a
// workspace per pane.

import "./style.css";
import {
  api, apiOrToast, ApiError, basename, CLIENT, copyText, DEFAULT_PREFS, dirname, keepToken, PROXIED, redeemTicket, scope, setScope, toast, TOKEN,
  type FsChange, type Pane, type Prefs, type TermState,
} from "./api";
import { Binding } from "./binding";
import { renderOffer } from "./host-offer";
import { contextMenu } from "./context-menu";
import { acceptFromPage, openOutside, PIN, PIN_CWD, PIN_HOST, pinKey, revealOutside, THEMED_BY_PAGE } from "./embed";
import { applyTheme } from "./theme";
import { Tree, type EditMode } from "./tree";
import { Viewer } from "./viewer";
import { PathBar } from "./viewer-path";
import { Stream } from "./stream";
import { Upgrade } from "./upgrade";
import { commitPath, trashPaths } from "./file-actions";
import { refreshScope } from "./panel-refresh";
import { renderSetupNotice, renderUpdate, watchToolbeltWidth } from "./iterm-tools";
import { refreshRecovery, renderRecovery } from "./recovery";
import { renderWeb } from "./web-access";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

let term: TermState = { version: -1, bridge: false };
const binding = new Binding();        // the iTerm2 window this panel lives in (AC-36)
let unseen = false;                   // fbd has no state of that window yet
let unlinked = false;                 // unbound, and another window's state was left alone (AC-36)
let updateNote = "";                  // an upgrade waits for unsaved work (AC-34)
let key: string | null = null;        // state key of the pane shown (session or tmux pane)
let ws: Pane | null = null;           // its workspace as last read or written
let workspaceRun = 0;                // orders reads and accepted write replies, even after a revision reset
let prefs: Prefs = { ...DEFAULT_PREFS };
let saveTimer = 0;
let pinCwd = PIN_CWD;                 // the web app's pane folder, sent with the first workspace read
let filterTimer = 0;
/** A viewer window (AC-26) shows only tabs: `?view=<path>`, opened with a one-time code `v`. */
const params = new URLSearchParams(location.search);
const VIEW = params.get("view");

/** Say what is wrong instead of an endless "connecting…" (AC-28). */
const NOT_FOLLOWING = "Not following iTerm2";
let noticeTitle = "";

function notice(title: string, text: string) {
  noticeTitle = title;
  const el = $("notice");
  el.classList.toggle("on", !!title);
  el.innerHTML = "";
  if (title) el.append(Object.assign(document.createElement("b"), { textContent: title }), text);
  $("title").textContent = title || term.title || "";
}

/** The token was refused, or fbd did not prove it knows it. `retrying`: the stream keeps
 *  asking for a proof (no token goes out), so the panel comes back by itself if fbd does. */
function authFailed(retrying = false) {
  if (!retrying) stream.close();
  $("dot").className = "dot off";
  if (VIEW) notice("Viewer link expired", "Open the file again from the Files panel (⌘-click).");
  else notice("Outdated panel link", "fbd does not prove it knows this panel's token: the link is outdated, or another program holds port 47821. Restart iTerm2; a new link comes with it.");
}

// ── workspace sync ───────────────────────────────────────────────────────────

function snapshot(): Pane | null {
  if (!ws) return null;
  return {
    ...ws,
    root: tree.root?.path ?? ws.root,
    expanded: [...tree.expanded],
    selected: [...tree.selected],
    scroll: Math.round(tree.scroll),
    tabs: viewer.tabs.map((t) => ({ ...t })),
    active_tab: viewer.active,
  };
}

/** What a PUT would change (rev and timestamp excluded). */
const content = (p: Pane) => JSON.stringify({ ...p, rev: 0, updated: 0 });

function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => void saveNow(), 300);
}

async function saveNow() {
  clearTimeout(saveTimer);
  const snap = snapshot(), k = key;
  if (!snap || !k || content(snap) === content(ws!)) return;
  const previous = ws, run = workspaceRun;
  try {
    const r = await api<{ rev: number }>("PUT", "/api/workspace", { query: { key: k }, body: snap });
    if (key === k && ws === previous && run === workspaceRun) { workspaceRun++; ws = { ...snap, rev: r.rev }; }
  } catch (e) {
    // another panel (other iTerm2 window) or a cd won the race: take the server's version
    if (e instanceof ApiError && e.code === "stale_rev" && key === k && ws === previous && run === workspaceRun) {
      workspaceRun++;
      await applyWorkspace(e.body.current as Pane);
    }
  }
}

async function loadWorkspace(k: string) {
  const run = ++workspaceRun;
  // a pinned panel names its pane's folder once: a later reconnect must not undo a newer cd
  const cwd = pinCwd ?? undefined;
  pinCwd = null;
  const pane = await api<Pane>("GET", "/api/workspace", { query: { key: k, cwd } });
  if (key === k && run === workspaceRun) await applyWorkspace(pane);
}

async function applyWorkspace(pane: Pane) {
  const paneKey = key;
  const sameTree = ws?.root === pane.root && tree.root?.path === pane.root &&
    JSON.stringify([...tree.expanded].sort()) === JSON.stringify([...pane.expanded].sort());
  ws = pane; // restoring it below is then a no-op for saveNow
  if (sameTree) { tree.selected = new Set(pane.selected); tree.queue(); }
  else await tree.setRoot(pane.root, pane.expanded, pane.selected, pane.scroll);
  if (key !== paneKey || ws !== pane) return;
  if (JSON.stringify(viewer.tabs) !== JSON.stringify(pane.tabs) || viewer.active !== pane.active_tab) viewer.setTabs(pane.tabs, pane.active_tab);
  renderHeader(term);
}

// ── terminal state ───────────────────────────────────────────────────────────

function renderHeader(s: TermState) {
  $("dot").className = "dot" + (!s.bridge ? " off" : s.stale ? " stale" : "");
  $("dot").title = !s.bridge ? "iTerm2 bridge not connected" : s.stale ? "cwd unavailable" : "following the focused pane";
  $("mode").textContent = s.mode ?? "—";
  $("mode").className = "badge" + (s.mode === "remote" ? " warn" : "");
  $("title").textContent = s.title ?? "";
  const cwd = ws?.root ?? s.cwd ?? "";
  const home = scope ? undefined : cwd.match(/^\/Users\/[^/]+/)?.[0];
  const head = (scope ? `${s.remote?.name ?? scope}:` : "") + (cwd === "/" ? "" : dirname(cwd).replace(/\/?$/, "/"));
  const b = Object.assign(document.createElement("b"), { textContent: basename(cwd) });
  const bdi = document.createElement("bdi");
  bdi.append(home ? head.replace(home, "~") : head, b);
  $("crumbs").replaceChildren(bdi);
  $("crumbs").title = cwd;
  $("note").textContent = [updateNote, unseen && "Switch to this window to update", unlinked && "Click here to follow this window",
    s.stale && "⏸ cwd unavailable — showing last known"].filter(Boolean).join(" · ");
  $("foot").textContent = s.note ?? "";  // the bridge's note on the pane (pid, job, host)
}

/** fbd answers, but nothing tells it where the terminal is: say so, the tree stays usable (AC-30). */
let bridgeCheck = 0;

/** A freshly (re)started fbd has not heard from the bridge yet: give it the same grace as a
 *  backend gap before saying the bridge is missing (AC-34). */
function bridgeNotice(s: TermState) {
  clearTimeout(bridgeCheck);
  const wait = stream.openedAt + 3000 - Date.now();
  if (!s.bridge && wait > 0) bridgeCheck = window.setTimeout(() => bridgeNotice(term), wait);
  else if (!s.bridge) notice(NOT_FOLLOWING, "Bridge not running: Scripts → AutoLaunch → fb_bridge.py, or restart iTerm2.");
  else if (noticeTitle === NOT_FOLLOWING) notice("", "");
}

/** Show the last state of this panel's own window when another window's is on screen. */
async function showOwnWindow() {
  if (!binding.window || term.window === binding.window) return;
  const s = await api<TermState>("GET", "/api/state", { query: { window: binding.window } });
  unseen = s.window !== binding.window;
  if (unseen) renderHeader(term); else await onState(s);
}

/** The user acted in the panel, so its window is key: bind to it (AC-36). */
function confirmWindow() {
  if (VIEW || PIN) return;
  void binding.confirm().then((asked) => {
    if (!asked) return;
    if (binding.window && unlinked) { unlinked = false; renderHeader(term); }
    return showOwnWindow();
  }).catch(() => {});
}

async function onState(s: TermState) {
  renderSetupNotice(s.setup_notice); renderUpdate(s.update); renderWeb(s.web);
  upgrade.build(s.build);
  if (PIN) { term = { ...term, bridge: s.bridge }; if (!THEMED_BY_PAGE && applyTheme(s.theme)) tree.themeChanged(); return renderHeader(term); }
  if (!VIEW && !binding.accepts(s)) { // another window's pane (AC-36): only the bridge status counts here
    const foreign = binding.foreign(s);
    if (s.bridge === term.bridge && foreign === unlinked) return;
    unlinked = foreign;
    term = { ...term, bridge: s.bridge };
    renderHeader(term);
    return bridgeNotice(term);
  }
  term = s;
  unseen = false;
  unlinked = false;
  binding.shown(s);
  const restyled = !THEMED_BY_PAGE && applyTheme(s.theme);
  if (VIEW) return; // the viewer window only takes the theme
  if (restyled) tree.themeChanged(); // row height follows the font size
  renderHeader(s);
  bridgeNotice(s);
  renderOffer($("offer"), s.remote);
  if (!s.key || !s.cwd) {
    if (!key) $("empty").textContent = s.bridge ? "Focus a local terminal pane" : "Waiting for iTerm2…";
    return;
  }
  $("empty").textContent = "";
  if (s.key === key && (s.host ?? null) === scope) return; // same pane and host: cwd changes arrive as "workspace"
  const leaving = key, snap = snapshot();
  key = s.key;                // switch at once: a quick A→B→A must end on A
  if (leaving && snap && ws && content(snap) !== content(ws)) {
    void api("PUT", "/api/workspace", { query: { key: leaving }, body: snap }).catch(() => {});
  }
  setScope(s.host ?? null);   // the new pane's files: this Mac's or a host's (AC-37)
  viewer.setHost(scope);
  ws = null;
  await loadWorkspace(s.key);
}

function onFsChange(c: FsChange) {
  if ((c.host ?? null) !== scope) return; // another host's files (AC-37)
  for (const { from, to } of c.moved) { tree.renamed(from, to); viewer.renamed(from, to); }
  void tree.refreshDirs(c.dirs);
  viewer.diskChanged(c.files);
}

let lastUnproven = false;
/** The event stream's handlers; it opens after fbd proved itself (stream.ts, AC-07). */
const stream = new Stream({
  on: {
    state: (s) => void onState(s),
    recovery: renderRecovery,
    // our own writes echo back with by === CLIENT
    workspace: ({ key: k, rev, by }) => { if (k === key && by !== CLIENT && (!ws || rev > ws.rev)) void loadWorkspace(k); },
    "fs-change": onFsChange,
    rescan: ({ host }) => refreshScope(host ?? null, scope, tree, viewer),
    "viewer-open": ({ path, host }) => { if (VIEW && (host ?? null) === scope) viewer.open(path); },
    "bridge-error": ({ message, by }) => { if (by === CLIENT) toast(message); }, // only the panel that asked
    unbind: ({ clients }) => { if (clients.includes(CLIENT)) binding.lost(); },
  },
  open() {
    notice("", "");
    if (upgrade.back()) { // fbd was away: what changed meanwhile was not announced
      refreshScope(scope, scope, tree, viewer);
    }
    if (VIEW) {
      const host = scope;
      return void api<string[]>("GET", "/api/view/pending", { host }).then((ps) => { if (host === scope) ps.forEach((p) => viewer.open(p)); }, () => {});
    }
    if (PIN) {                 // the web app's pane (AC-53): no window to bind, no recovery here
      key = pinKey(PIN, PIN_HOST); setScope(PIN_HOST); viewer.setHost(scope);
      return void loadWorkspace(key);
    }
    void refreshRecovery();
    void binding.claim().then(() => showOwnWindow()).catch(() => {});
    renderHeader(term);
    if (key) void loadWorkspace(key);
  },
  lost(unproven) {  // say so after the grace period (the latest reason); the stream tries again by itself
    lastUnproven = unproven;
    $("dot").className = "dot wait"; // a restart or an upgrade takes a moment (AC-34)
    upgrade.lost(() => {
      $("dot").className = "dot off";
      if (lastUnproven) authFailed(true);
      else notice("Backend not running", "fbd is not reachable; retrying. Check that iTerm2's Python API is enabled and see ~/.iterm-enhancer/logs/fbd.log.");
    });
  },
});

// ── file operations ──────────────────────────────────────────────────────────

function commit(mode: EditMode, dir: string, name: string, path?: string): Promise<string | null> {
  const host = scope, paneKey = key;
  return commitPath(mode, dir, name, path, {
    host, current: () => scope === host && key === paneKey, tree, viewer, changed: scheduleSave,
  });
}

/** The pane changed hosts while the user was deciding: the decision was about other files. */
function switchedSince(host: string | null) {
  if (scope === host) return false;
  toast("The panel switched to another pane — nothing changed");
  return true;
}

async function trashSelected() {
  const host = scope, paneKey = key;
  const paths = [...tree.selected];
  if (!paths.length) return;
  if (paths.some((p) => !tree.isWritable(dirname(p)))) return toast("Read-only: outside writable folders");
  const current = () => !switchedSince(host) && key === paneKey;
  const removed = await trashPaths(paths, host, paths.flatMap((p) => viewer.dirtyUnder(p)), current);
  if (!removed || !current()) return;
  viewer.removed(removed);
  tree.selected.clear();
  await tree.refreshDirs([...new Set(paths.map(dirname))]);
  scheduleSave();
}

const copyPaths = (text: string) => copyText(text, text.includes("\n") ? `Copied ${text.split("\n").length} paths` : `Copied ${text}`);

const relative = (p: string) => {
  const root = tree.root?.path ?? "";
  return p === root ? "." : p.startsWith(root + "/") ? p.slice(root.length + 1) : p;
};

/** fbd quotes paths and refuses if focus moved to another pane than the one shown. */
const terminal = (action: "insert" | "cd", body: object) => {
  // an unbound panel may show another window's pane: never type there for a click made here
  if (!binding.window) return toast("Click here to follow this window first — nothing typed");
  return apiOrToast("POST", `/api/terminal/${action}`, { body: { ...body, key } });
};

// ── layout ───────────────────────────────────────────────────────────────────

const savePrefs = () => void api("PUT", "/api/prefs", { body: prefs }).catch(() => {});

function setSplit(r: number) {
  prefs.split = Math.min(0.85, Math.max(0.15, r));
  $("app").style.setProperty("--split", `${prefs.split * 100}%`);
}

function initSplitter() {
  const sp = $("splitter");
  sp.addEventListener("pointerdown", (e) => {
    sp.setPointerCapture(e.pointerId);
    const app = $("app").getBoundingClientRect(), top = $("tree").getBoundingClientRect().top;
    const move = (ev: PointerEvent) => setSplit((ev.clientY - top) / (app.bottom - top));
    const up = () => { sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up); savePrefs(); };
    sp.addEventListener("pointermove", move);
    sp.addEventListener("pointerup", up);
  });
  sp.addEventListener("dblclick", () => { setSplit(0.5); savePrefs(); });
}

// ── wiring ───────────────────────────────────────────────────────────────────

const openWindow = (path: string) => void (openOutside(path, scope) || apiOrToast("POST", "/api/view/open", { body: { path, host: scope } }));
const openFile = (path: string) => { if (!openOutside(path, scope)) viewer.open(path); };

const tree = new Tree($("tree"), {
  open: openFile,
  openWindow,
  changed: scheduleSave,
  context: (p, isDir, ev) => void contextMenu(p, isDir, ev, {
    tree, term: () => term, switchedSince, open: openFile, openWindow, trash: () => void trashSelected(),
    copy: (t) => void copyPaths(t), relative, terminal,
  }),
  commit,
  notify: toast,
});

const viewer = new Viewer($("tabs"), $("tools"), $("vbody"), {
  changed: scheduleSave,
  layout: (has) => $("app").classList.toggle("has-tabs", has),
  reveal: (p) => { if (!revealOutside(p)) revealInTree(p); },
  active: (p) => pathBar?.show(p),
});
let pathBar: PathBar | null = null; // viewer window only
function revealInTree(p: string) { tree.selected = new Set([p]); void tree.reveal(p); scheduleSave(); }

const upgrade = new Upgrade({
    busy: () => viewer.hasDirty || !!document.querySelector(".modal, .inline-edit.on, dialog[open]"),
  beforeReload: () => saveNow(),
  waiting: (text) => {
    if (updateNote) return;
    updateNote = text;
    if (VIEW) toast(text); else renderHeader(term);
  },
});

document.addEventListener("pointerdown", confirmWindow, true);
document.addEventListener("keydown", () => { if (!binding.window) confirmWindow(); }, true); // keys reach only the key window

const filterEl = $<HTMLInputElement>("filter");
filterEl.addEventListener("input", () => {
  clearTimeout(filterTimer);
  filterTimer = window.setTimeout(() => tree.setFilter(filterEl.value), 150);
});
filterEl.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { filterEl.value = ""; tree.setFilter(""); tree.focus(); }
  if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); tree.focus(); }
});
const newItem = (mode: "file" | "folder") => { const d = tree.targetDir(); if (d) void tree.startCreate(d, mode); };
$("refresh").onclick = () => { tree.reload(); viewer.reloadAll(); };
$("collapse").onclick = () => tree.collapseAll();
$("expand").onclick = () => void tree.expandAll().then((m) => { if (m) toast(m); });
$("new-file").onclick = () => newItem("file");
$("new-folder").onclick = () => newItem("folder");
$("hidden").onclick = () => {
  prefs.hidden = !prefs.hidden;
  $("hidden").classList.toggle("on", !prefs.hidden);
  tree.setShowHidden(prefs.hidden);
  savePrefs();
};
$("maximize").onclick = () => $("app").classList.toggle("max");

// Keyboard. ⌘-shortcuts that iTerm2 owns (⌘N, ⌘W, ⌘T) are avoided; ⌥-variants are used
// instead. ⌘S is also bound inside the editor.
document.addEventListener("keydown", (e) => {
  if (document.querySelector("dialog[open]")) return;
  const t = e.target as HTMLElement;
  const inField = t.matches("input, textarea") || !!t.closest(".cm-editor");
  if (e.key === "/" && !inField) { e.preventDefault(); filterEl.focus(); return; }
  if (e.key === "Escape" && t.closest("#viewer") && !t.closest(".cm-panels")) { tree.focus(); return; }
  if (inField) return;
  const inTree = !!t.closest("#tree");
  if (e.altKey && !e.metaKey && e.code === "KeyN") { e.preventDefault(); newItem(e.shiftKey ? "folder" : "file"); }
  else if (e.metaKey && e.key === "Backspace" && inTree) { e.preventDefault(); void trashSelected(); }
  else if (e.metaKey && e.key === "Enter" && inTree) {
    e.preventDefault();
    const p = tree.cursorPath;
    if (p && tree.targetDir() !== p) openWindow(p); // a file, not a folder
  }
  else if (e.metaKey && e.altKey && e.code === "KeyC" && inTree) {
    e.preventDefault();
    const sel = [...tree.selected];
    void copyPaths((e.shiftKey ? sel.map(relative) : sel).join("\n"));
  }
  else if (e.ctrlKey && e.key === "Tab") { e.preventDefault(); viewer.cycle(e.shiftKey ? -1 : 1); }
  else if (e.altKey && e.code === "KeyW") { e.preventDefault(); viewer.closeActive(); }
  else if (e.metaKey && e.key.toLowerCase() === "s" && viewer.active != null) {
    e.preventDefault();
    void viewer.save(viewer.tabs[viewer.active].path);
  }
});

window.addEventListener("beforeunload", (e) => {
  void saveNow();
  if (viewer.hasDirty) e.preventDefault();
});

async function startViewer(path: string) {
  document.documentElement.classList.add("viewer-mode");
  setScope(params.get("host")); // a remote file's viewer window (AC-37)
  viewer.setHost(scope);
  pathBar = new PathBar($("pathbar"), copyPaths);
  pathBar.show(path);
  const code = params.get("v");
  if (code && !TOKEN) {
    try { await redeemTicket(code); } catch { return authFailed(); }
  }
  if (!TOKEN && !PROXIED) return authFailed();

  keepToken(); // reloads keep working; the URL keeps no secret
  void stream.connect();
  viewer.open(path, "auto");
}

acceptFromPage({ open: (p) => viewer.open(p), reveal: revealInTree, theme: (t) => { if (applyTheme(t) && !VIEW) tree.themeChanged(); } });

(async () => {
  if (VIEW) return startViewer(VIEW);
  keepToken();
  initSplitter();
  // this window starts from the latest layout; changing it later updates the default only
  let denied = false;
  const saved = await api<Partial<Prefs> | null>("GET", "/api/prefs").catch((e: ApiError) => { denied = e.status === 401; return null; });
  if (denied) return authFailed();
  prefs = { ...DEFAULT_PREFS, ...(saved ?? {}) };
  setSplit(prefs.split);
  tree.showHidden = prefs.hidden;
  $("hidden").classList.toggle("on", !prefs.hidden);
  tree.themeChanged();
  watchToolbeltWidth();
  void stream.connect();
})().catch((e) => toast(String(e)));
