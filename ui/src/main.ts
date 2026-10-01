// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Files panel: follows the focused terminal pane (pushed by fbd over SSE), shows its
// directory as a virtual tree, opens files in tabs, edits and saves them, and keeps a
// workspace per pane.

import "./style.css";
import {
  api, apiOrToast, ApiError, basename, CLIENT, DEFAULT_PREFS, dirname, eventsUrl, keepToken, redeemTicket, scope, setScope, toast, TOKEN,
  type FsChange, type Pane, type Prefs, type TermState,
} from "./api";
import { Binding } from "./binding";
import { hostEntries, hostMenu, renderOffer } from "./host-offer";
import { ask, menu, type MenuEntry } from "./dialogs";
import { applyTheme } from "./theme";
import { Tree, type EditMode } from "./tree";
import { Viewer } from "./viewer";
import { PathBar } from "./viewer-path";
import { Upgrade } from "./upgrade";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

let term: TermState = { version: -1, bridge: false };
const binding = new Binding();        // the iTerm2 window this panel lives in (AC-36)
let unseen = false;                   // fbd has no state of that window yet
let updateNote = "";                  // an upgrade waits for unsaved work (AC-34)
let key: string | null = null;        // state key of the pane shown (session or tmux pane)
let ws: Pane | null = null;           // its workspace as last read or written
let prefs: Prefs = { ...DEFAULT_PREFS };
let saveTimer = 0;
let filterTimer = 0;
let es: EventSource | null = null;
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

function authFailed() {
  es?.close();
  $("dot").className = "dot off";
  if (VIEW) notice("Viewer link expired", "Open the file again from the Files panel (⌘-click).");
  else notice("Outdated panel link", "fbd no longer accepts this panel's token. Reopen it (View → Toolbelt → uncheck and check Files), or restart iTerm2.");
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
  try {
    const r = await api<{ rev: number }>("PUT", "/api/workspace", { query: { key: k }, body: snap });
    if (key === k) ws = { ...snap, rev: r.rev };
  } catch (e) {
    // another panel (other iTerm2 window) or a cd won the race: take the server's version
    if (e instanceof ApiError && e.code === "stale_rev" && key === k) await applyWorkspace(e.body.current as Pane);
  }
}

async function loadWorkspace(k: string) {
  const pane = await api<Pane>("GET", "/api/workspace", { query: { key: k } });
  if (key === k) await applyWorkspace(pane);
}

async function applyWorkspace(pane: Pane) {
  const sameTree = ws?.root === pane.root && tree.root?.path === pane.root &&
    JSON.stringify([...tree.expanded].sort()) === JSON.stringify([...pane.expanded].sort());
  ws = pane; // restoring it below is then a no-op for saveNow
  if (sameTree) { tree.selected = new Set(pane.selected); tree.queue(); }
  else await tree.setRoot(pane.root, pane.expanded, pane.selected, pane.scroll);
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
  $("note").textContent = updateNote + (unseen ? "Switch to this window to update · " : "") +
    (s.stale ? "⏸ cwd unavailable — showing last known · " : "") + (s.note ?? "");
}

/** fbd answers, but nothing tells it where the terminal is: say so, the tree stays usable (AC-30). */
function bridgeNotice(s: TermState) {
  if (!s.bridge) notice(NOT_FOLLOWING, "Bridge not running: Scripts → AutoLaunch → fb_bridge.py, or restart iTerm2.");
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
  if (!VIEW) void binding.confirm().then((asked) => { if (asked) return showOwnWindow(); }).catch(() => {});
}

async function onState(s: TermState) {
  upgrade.build(s.build);
  if (!VIEW && !binding.accepts(s)) { // another window's pane (AC-36): only the bridge status counts here
    if (s.bridge === term.bridge) return;
    term = { ...term, bridge: s.bridge };
    renderHeader(term);
    return bridgeNotice(term);
  }
  term = s;
  unseen = false;
  const restyled = applyTheme(s.theme);
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
  if (s.key === key) return; // same pane, new cwd: fbd resets the workspace and sends "workspace"
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

function connect() {
  es = new EventSource(eventsUrl());
  const on = (name: string, fn: (data: any) => void) => es!.addEventListener(name, (e) => fn(JSON.parse((e as MessageEvent).data)));
  on("state", (s) => void onState(s));
  // our own writes echo back with by === CLIENT
  on("workspace", ({ key: k, rev, by }) => { if (k === key && by !== CLIENT && (!ws || rev > ws.rev)) void loadWorkspace(k); });
  on("fs-change", onFsChange);
  on("viewer-open", ({ path, host }) => { if (VIEW && (host ?? null) === scope) viewer.open(path); });
  on("bridge-error", ({ message, by }) => { if (by === CLIENT) toast(message); }); // only the panel that asked
  on("unbind", ({ clients }) => { if (clients.includes(CLIENT)) binding.lost(); });
  es.onerror = () => {
    $("dot").className = "dot wait"; // a restart or an upgrade takes a moment (AC-34)
    upgrade.lost(() => {
      $("dot").className = "dot off";
      // EventSource hides the status: ask once whether the token or the backend is the problem
      api("GET", "/api/state", { retry: false }).then(() => {}, (e: ApiError) => {
        if (e.status === 401) authFailed();
        else notice("Backend not running", "fbd is not reachable; retrying. Check that iTerm2's Python API is enabled and see ~/.iterm-filebrowser/logs/fbd.log.");
      });
    });
  };
  es.onopen = () => {
    notice("", "");
    if (upgrade.back()) { // fbd was away: what changed meanwhile was not announced
      viewer.recheck();
      if (tree.root) void tree.refreshDirs([tree.root.path, ...tree.expanded]);
    }
    if (VIEW) return void api<string[]>("GET", "/api/view/pending").then((ps) => ps.forEach((p) => viewer.open(p)), () => {});
    void binding.claim().then(() => showOwnWindow()).catch(() => {});
    renderHeader(term);
    if (key) void loadWorkspace(key);
  };
}

// ── file operations ──────────────────────────────────────────────────────────

async function commit(mode: EditMode, dir: string, name: string, path?: string): Promise<string | null> {
  try {
    let created: string;
    if (mode === "rename") {
      if (!path || name === basename(path)) return null;
      created = (await api<{ path: string }>("POST", "/api/fs/rename", { body: { path, name } })).path;
      tree.renamed(path, created);
      viewer.renamed(path, created);
    } else {
      created = (await api<{ path: string }>("POST", `/api/fs/${mode === "folder" ? "mkdir" : "touch"}`, { body: { parent: dir, name } })).path;
      if (mode === "file") viewer.open(created);
    }
    await tree.refreshDirs([dir]);
    tree.selected = new Set([created]);
    await tree.reveal(created);
    scheduleSave();
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}

async function trashSelected() {
  const paths = [...tree.selected];
  if (!paths.length) return;
  if (paths.some((p) => !tree.isWritable(dirname(p)))) return toast("Read-only: outside writable folders");
  const dirty = paths.flatMap((p) => viewer.dirtyUnder(p));
  const what = paths.length === 1 ? `"${basename(paths[0])}"` : `${paths.length} items`;
  const extra = dirty.length ? `${dirty.length} unsaved file${dirty.length > 1 ? "s" : ""} will be lost. ` : "";
  const ok = await ask(`Move ${what} to Trash?`, `${extra}You can restore from the Trash in Finder.`,
    [{ id: "trash", label: "Move to Trash", danger: true, primary: true }, { id: "cancel", label: "Cancel" }]);
  if (ok !== "trash") return;
  type Failed = { failed: { path: string; message: string }[] };
  const r: Failed = await api<Failed>("POST", "/api/fs/trash", { body: { paths } })
    .catch((e: ApiError) => (e.body?.failed ? e.body : { failed: [{ path: "", message: e.message }] }));
  if (r.failed.length) toast(r.failed.map((f) => f.message).join("; "));
  viewer.removed(paths.filter((p) => !r.failed.some((f) => f.path === p)));
  tree.selected.clear();
  await tree.refreshDirs([...new Set(paths.map(dirname))]);
  scheduleSave();
}

async function copyText(text: string) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = Object.assign(document.createElement("textarea"), { value: text });
    document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast(text.includes("\n") ? `Copied ${text.split("\n").length} paths` : `Copied ${text}`);
}

const relative = (p: string) => {
  const root = tree.root?.path ?? "";
  return p === root ? "." : p.startsWith(root + "/") ? p.slice(root.length + 1) : p;
};

/** fbd quotes paths and refuses if focus moved to another pane than the one shown. */
const terminal = (action: "insert" | "cd", body: object) => apiOrToast("POST", `/api/terminal/${action}`, { body: { ...body, key } });

async function contextMenu(path: string | null, isDir: boolean, ev: MouseEvent) {
  const sel = path ? [...tree.selected] : [];
  const many = sel.length > 1;
  const target = path ?? tree.root?.path ?? "";
  const dir = path && !isDir ? dirname(path) : target;
  const canWrite = tree.isWritable(dir);
  const entries: MenuEntry[] = [
    { id: "new-file", label: "New File…", keys: "⌥N", disabled: !canWrite },
    { id: "new-folder", label: "New Folder…", keys: "⌥⇧N", disabled: !canWrite },
  ];
  if (path) entries.push(
    "-",
    ...(!isDir && !many ? [{ id: "open", label: "Open", keys: "↩" }, { id: "open-window", label: "Open in Window", keys: "⌘↩" }] : []),
    { id: "rename", label: "Rename…", keys: "F2", disabled: many || !tree.isWritable(dirname(path)) },
    { id: "trash", label: many ? `Move ${sel.length} Items to Trash` : "Move to Trash", keys: "⌘⌫", danger: true, disabled: !sel.every((p) => tree.isWritable(dirname(p))) },
    "-",
    { id: "copy-path", label: many ? `Copy ${sel.length} Paths` : "Copy Path", keys: "⌥⌘C" },
    { id: "copy-rel", label: many ? `Copy ${sel.length} Relative Paths` : "Copy Relative Path", keys: "⌥⇧⌘C" },
    ...(scope ? [] : [{ id: "reveal", label: "Reveal in Finder", disabled: many }, { id: "open-app", label: "Open with Default App", disabled: many }]),
    "-",
    { id: "insert", label: "Insert Path in Terminal" },
    { id: "cd", label: "Open Terminal Here", disabled: many },
  );
  else entries.push("-", ...(scope ? [] : [{ id: "reveal", label: "Reveal in Finder" }]), { id: "cd", label: "Open Terminal Here" });
  entries.push(...hostEntries(term.remote));
  const choice = await menu(ev.clientX, ev.clientY, entries);
  if (choice?.startsWith("host-") && term.remote) return void hostMenu(choice, term.remote);
  switch (choice) {
    case "new-file": return void tree.startCreate(dir, "file");
    case "new-folder": return void tree.startCreate(dir, "folder");
    case "open": return viewer.open(target);
    case "open-window": return openWindow(target);
    case "rename": return tree.startRename(target);
    case "trash": return void trashSelected();
    case "copy-path": return void copyText(sel.join("\n"));
    case "copy-rel": return void copyText(sel.map(relative).join("\n"));
    case "reveal": return void apiOrToast("POST", "/api/os/reveal", { body: { path: target } });
    case "open-app": return void apiOrToast("POST", "/api/os/open", { body: { path: target } });
    case "insert": return void terminal("insert", { paths: sel.map(relative) });
    case "cd": return void terminal("cd", { path: dir });
  }
}

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

const openWindow = (path: string) => void apiOrToast("POST", "/api/view/open", { body: { path, host: scope } });

const tree = new Tree($("tree"), {
  open: (p) => viewer.open(p),
  openWindow,
  changed: scheduleSave,
  context: (p, isDir, ev) => void contextMenu(p, isDir, ev),
  commit,
  notify: toast,
});

const viewer = new Viewer($("tabs"), $("tools"), $("vbody"), {
  changed: scheduleSave,
  layout: (has) => $("app").classList.toggle("has-tabs", has),
  reveal: (p) => { tree.selected = new Set([p]); void tree.reveal(p); scheduleSave(); },
  active: (p) => pathBar?.show(p),
});
let pathBar: PathBar | null = null; // viewer window only

const upgrade = new Upgrade({
  busy: () => viewer.hasDirty || !!document.querySelector(".modal, .inline-edit.on"),
  beforeReload: () => saveNow(),
  waiting: (text) => {
    if (updateNote) return;
    updateNote = text + " · ";
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
    void copyText((e.shiftKey ? sel.map(relative) : sel).join("\n"));
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

/** The user widened or narrowed this Toolbelt: make it the width of new windows (AC-27). */
function watchToolbeltWidth() {
  let reported = innerWidth, timer = 0;
  addEventListener("resize", () => {
    clearTimeout(timer);
    timer = window.setTimeout(() => {
      if (Math.abs(innerWidth - reported) < 4) return;
      reported = innerWidth;
      void api("POST", "/api/ui/toolbelt-width", { body: {} }).catch(() => {});
    }, 800);
  });
}

async function startViewer(path: string) {
  document.documentElement.classList.add("viewer-mode");
  setScope(params.get("host")); // a remote file's viewer window (AC-37)
  viewer.setHost(scope);
  pathBar = new PathBar($("pathbar"), copyText);
  pathBar.show(path);
  const code = params.get("v");
  if (code && !TOKEN) {
    try { await redeemTicket(code); } catch { return authFailed(); }
  }
  if (!TOKEN) return authFailed();
  keepToken(); // reloads keep working; the URL keeps no secret
  connect();
  viewer.open(path, "auto");
}

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
  connect();
})().catch((e) => toast(String(e)));
