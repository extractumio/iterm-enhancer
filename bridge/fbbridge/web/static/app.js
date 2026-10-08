// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
import { Input, shellWord } from "./input.js";
import { findItem, metaLine, profileDot, renderNav, stateIcon } from "./nav.js";
import { textForm } from "./render.js";
import { arrowsTo, cellAt, cursorCell, inOneBox } from "./cursor.js";
import { navTools } from "./navtools.js";
import { bindReorder, ordered } from "./reorder.js";
import { bindLinks } from "./termlinks.js";
import { copyButton } from "./touchcopy.js";
import { TermView } from "./term.js";
import { Views } from "./views.js";

const $ = (id) => document.getElementById(id);
const narrow = matchMedia("(max-width: 860px)");
const touch = matchMedia("(hover: none)");

const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const st = {
  ws: null, groups: [], sid: store.get("sid", null),
  mode: store.get("mode", {}), size: store.get("size", null), resized: false,
  collapsed: new Set(store.get("collapsed", [])),   // session groups closed in the list
  appCursor: false, bracketed: store.get("bracketed", true), showInIterm: store.get("showInIterm", true),
};

// A phone keeps fewer lines on the page: each one costs on every touch (AC-52).
const view = new TermView({ term: $("term"), hist: $("hist"), screen: $("screen"), note: $("note"),
  bottomBtn: $("bottom"), paused: $("paused"), send, keep: touch.matches ? 2000 : Infinity });
const input = new Input({ kbd: $("kbd"), kbdButton: $("kbdbtn"), term: $("term"), panel: $("hotkeys"), pasteDialog: $("pastebox"),
  send: (data) => { send({ t: "in", data }); if (data.includes("\r")) views.commandSent(); },
  options: { appCursor: () => st.appCursor, bracketed: () => st.bracketed, onTyped: () => view.typed(), status: toast, pasteImage, pickFile,
    tapLink: (x, y) => tapLink(x, y), tapMove: (x, y) => tapMove(x, y) } });
copyButton($("copyfab"), $("term"), (text) => input.copySelection(text));

const views = new Views({ send, store, onShow: () => view.keepBottom(applyFit) });
// Addresses open in a new tab here, paths in File (on the pane's host).
const tapLink = bindLinks($("term"), { paneInfo: () => views.paneInfo(), openFile: (p, host) => views.open(p, host), toast });

// A tap (or ⌥-click) on the input moves the cursor there with arrow keys; across rows only in
// a coding agent's pane, where ↑ and ↓ move in the input instead of recalling shell history.
// Only where arrows edit a line: a coding agent's input or a shell's prompt (not htop, less, mc).
// Rows only inside one box drawn between two rules: ↑ on an input's first line, or anywhere
// in a shell, would recall history instead of moving.
function tapMove(x, y) {
  const found = st.sid && findItem(st.groups, st.sid);
  if (!found?.item.agent && !found?.item.shell) return;
  const from = cursorCell($("screen")), to = cellAt($("screen"), x, y);
  const vertical = !!found.item.agent && !!from && !!to && inOneBox($("screen"), from.row, to.row);
  const seq = arrowsTo(from, to, { vertical, appCursor: st.appCursor });
  if (seq) send({ t: "in", data: seq });
  if (seq) view.typed();
}
$("term").addEventListener("click", (e) => { if (e.altKey && !String(getSelection())) tapMove(e.clientX, e.clientY); });

// On a touch screen the invisible keyboard field lies over the cursor's row, so a long press
// there gets iOS's own Paste (text, or an image); elsewhere it is out of the way.
function placeKbd() {
  const k = $("kbd"), cur = touch.matches && $("screen").querySelector(".cur");
  const t = $("term").getBoundingClientRect(), r = cur?.getBoundingClientRect();
  if (!r || r.bottom < t.top || r.top > t.bottom) { k.style.cssText = ""; return; }
  k.style.cssText = `left:${t.left}px;top:${r.top}px;width:${t.width}px;height:${r.height}px`;   // the cursor's row only
}
view.onDrawn = placeKbd;
let kbdFrame = 0;
$("term").addEventListener("scroll", () => { if (!kbdFrame) kbdFrame = requestAnimationFrame(() => { kbdFrame = 0; placeKbd(); }); }, { passive: true });

let toastTimer = 0;
function toast(text, ms = 1800) {
  $("toast").textContent = text; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, ms);
}

// A pasted image is saved where the shown pane's shell runs (this Mac or its host), and its
// path is pasted as text: programs such as Claude Code take an image by its path.
// "Upload" on the hot keys does the same for a file of any kind, keeping its name.
function pasteImage(blob) { return sendFile(blob, "image", "/paste?sid="); }      // declared: Input takes it before this line runs
function uploadFile(file) { return sendFile(file, "file", `/upload?name=${encodeURIComponent(file.name)}&sid=`); }
function pickFile() { $("upload").value = ""; $("upload").click(); }
$("upload").addEventListener("change", () => { const f = $("upload").files[0]; if (f) uploadFile(f); });

async function sendFile(blob, what, url) {
  const sid = st.sid;
  if (!sid) return toast("Open a session first.");
  toast(`Uploading the ${what}…`, 60000);
  const r = await fetch(url + encodeURIComponent(sid), { method: "POST", headers: { "Content-Type": blob.type || "application/octet-stream" }, body: blob })
    .catch(() => null);
  const text = r ? await r.text().catch(() => "") : "";
  let m = {};
  try { m = JSON.parse(text); } catch { m = { error: text.trim() || undefined }; }   // the server's own refusals are plain text
  if (!r?.ok || !m.path) return toast(m.error ?? (r ? `The ${what} was not uploaded (${r.status}).` : "The Mac does not answer."), 6000);
  if (st.sid !== sid) return toast(`The ${what} was saved, but another session is shown now: nothing was pasted.`, 6000);
  $("toast").hidden = true;
  input.paste(shellWord(m.path));
}

// ---------- connection and sign-in ----------
// The sign-in is an HttpOnly cookie: this script never holds it. The WebSocket and the Files
// frames carry it by themselves; a refused WebSocket means "sign in again" or "Mac away".

let retry = 500;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
  st.ws = ws;
  ws.onopen = () => { retry = 500; status(""); signedIn(); };
  ws.onmessage = (ev) => onMessage(JSON.parse(ev.data));
  ws.onclose = async () => {
    if (st.ws !== ws) return;
    const check = await fetch("/auth/check").then((r) => r.status, () => 0);
    if (check === 401) return showLogin();
    status("Lost the connection to the Mac. Reconnecting…");
    setTimeout(connect, retry = Math.min(retry * 2, 8000));
  };
}
function send(m) { if (st.ws?.readyState === 1) st.ws.send(JSON.stringify(m)); }
function status(text) { $("status").hidden = !text; $("status").textContent = text; }

function showLogin(message = "") {
  $("app").hidden = true; $("login").hidden = false;
  $("loginerr").textContent = message;
  $("password").focus();
}

$("login").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("loginerr").textContent = "";
  const r = await fetch("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("password").value }) }).catch(() => null);
  if (!r) return showLogin("The Mac does not answer. Check that web access is on.");
  if (r.ok) { $("password").value = ""; return connect(); }
  const m = await r.json().catch(() => ({}));
  const why = { wrong: "Wrong password.", locked: "Too many wrong passwords." }[m.reason] ?? "Sign-in failed.";
  showLogin(why + (m.retry ? ` Try again in ${m.retry} s.` : ""));
});

const tools = navTools({ send, store, current: () => (st.sid && findItem(st.groups, st.sid)) || null,
  onResize: () => view.keepBottom(applyFit) });

function signedIn() {
  $("login").hidden = true; $("app").hidden = false;
  send({ t: "prefs", showInIterm: st.showInIterm });
  if (st.sid) select(st.sid, true);
}

// ---------- updates ----------
// The bridge names the build of the page's files first on every connection. A page of another
// build (open across an upgrade, or restored from memory) reloads once, but never while a
// Files or File frame has unsaved edits, a dialog or an inline edit: it waits for those.
const BUILD = document.querySelector('meta[name="build"]').content;
let updateTimer = 0;
function checkBuild(id) {
  if (id === BUILD || updateTimer) return;
  if (sessionStorage.getItem("web.reloaded-for") === id) return status("An update is ready: reload the page.");   // no loop
  const attempt = () => {
    const busy = ["files", "file"].some((f) => { try { return $(f).contentWindow?.fbBusy?.(); } catch { return false; } });
    if (busy) return status("An update is ready: the page reloads after you save.");
    clearInterval(updateTimer);
    try { sessionStorage.setItem("web.reloaded-for", id); } catch { /* private mode: at worst asks again */ }
    location.reload();
  };
  updateTimer = setInterval(attempt, 1000);
  attempt();
}
// ---------- the keys go back to the terminal (a computer only) ----------
// With a mouse and a hardware keyboard, typing goes to the terminal again when the user comes
// back to this window or tab (if it had the keys before), picks a session, or returns to the
// Terminal view; never while a field, Files or File, the paste dialog, a name or a menu has
// them. A touch screen gets no focus() it did not tap for: on iOS that raises the keyboard.
const computer = matchMedia("(hover: hover) and (pointer: fine)");
let termHadKeys = false;
function focusTerminal() {
  if (!computer.matches || $("app").hidden || document.body.dataset.shown !== "term") return;
  if (!$("menu").hidden || $("pastebox").open || tools.renaming()) return;
  if (document.activeElement?.closest?.("input, textarea:not(#kbd), select, iframe, [contenteditable]")) return;
  input.focus();
}
addEventListener("blur", () => { termHadKeys = document.activeElement === $("kbd"); });   // leaving: who had the keys
addEventListener("focus", () => { if (termHadKeys) focusTerminal(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && termHadKeys) focusTerminal(); });
document.querySelector('button[data-view="term"]').addEventListener("click", () => focusTerminal());

// A page Safari brings back from memory connects again, and so learns the current build.
addEventListener("pageshow", (e) => { if (e.persisted && st.ws?.readyState !== WebSocket.OPEN) connect(); });

// A hidden page (another app, a locked phone) costs the Mac nothing: its stream pauses.
document.addEventListener("visibilitychange", () => send({ t: document.hidden ? "pause" : "resume" }));

function onMessage(m) {
  if (m.sid && m.sid !== st.sid) return;            // a frame from the session we just left
  switch (m.t) {
    case "layout": st.groups = m.groups; drawNav(); break;
    case "theme": applyTheme(m.theme); break;
    case "hist": view.onHist(m); break;
    case "screen": view.onScreen(m); break;
    case "fit": st.resized = m.on; syncControls(); break;
    case "files": views.onFiles(m); break;
    case "error": status(m.msg); break;
    case "drops": tools.showDrops(m.items); break;
    case "profiles": tools.showProfiles(m.items); break;
    case "build": checkBuild(m.id); break;
    case "created": select(m.id); closeDrawer(); break;
  }
}

// ---------- navigation ----------

// Rows reorder iTerm2's tabs in their window; groups reorder this browser's list only.
const reorder = bindReorder($("sessions"), (kind, parent, ids) => {
  if (kind === "group") store.set("groupOrder", ids);
  else send({ t: "reorder", group: parent.dataset.wid, ids });
}, () => drawNav());                                // a layout may have come while dragging

function drawNav() {
  if (reorder.dragging()) return;                  // the next layout redraws it
  renderNav($("sessions"), ordered(st.groups, store.get("groupOrder", [])), st.sid, $("filter").value, (id) => { select(id); closeDrawer(); focusTerminal(); }, st.collapsed, toggleGroup,
    (wid) => send({ t: "new", group: wid }));
  const found = st.sid && findItem(st.groups, st.sid);
  if (!found) {
    const first = st.groups.flatMap((g) => g.items)[0];
    if (first) select(first.id);
    else showCurrent(null);
    if (!st.sid && narrow.matches) openDrawer();
    return;
  }
  showCurrent(found);
}

function toggleGroup(key) {
  if (!st.collapsed.delete(key)) st.collapsed.add(key);
  store.set("collapsed", [...st.collapsed]);
  drawNav();
}

// The header: the agent's state, the title and the pencil; then one line that only shortens at
// its end (the folder): the profile's dot, profile, where the tab is, host, program, folder.
function showCurrent(found) {
  $("current").querySelector(".t").textContent = found ? textForm(found.item.title) : "Choose a session";
  $("curstate").replaceChildren(...[found && stateIcon(found.item.state)].filter(Boolean));
  if (!found) $("cursub").replaceChildren();
  else {
    const { group: g, item: it } = found;
    const where = (g.session ? `${g.label} ${g.session}` : g.label) + (it.index ? `, tab ${it.index}` : "");
    const line = metaLine([it.profile || "", where, ...(it.host ? [it.host] : []), ...it.sub].filter(Boolean), false);
    if (it.host) [...line.children].find((c) => c.textContent === it.host)?.classList.add("host");
    $("cursub").replaceChildren(profileDot(it.profile, false, it.state === "working"), ...line.childNodes);
  }
  document.title = found ? `${found.item.title} – iTerm2 Web` : "iTerm2 Web";
  if (!tools.renaming()) $("rename").hidden = !found;
}

function select(id, force = false) {
  if (id === st.sid && !force && view.screen.length) return;
  st.sid = id; store.set("sid", id);
  status("");                                      // an error about the pane shown before
  if (tools.renaming()) tools.endRename();         // a name being typed was for that pane
  view.reset();
  send({ t: "sub", id });                          // first: the server answers "files" for the subscribed pane
  views.sessionChanged();
  drawNav(); syncControls();
}

function openDrawer() {
  if (!narrow.matches) { $("app").classList.remove("nav-hidden"); store.set("navHidden", false); return; }
  $("app").classList.add("nav-open");
  $("navbtn").setAttribute("aria-expanded", "true");
  $("sessions").querySelector(".item.on")?.scrollIntoView({ block: "center" });
}
function closeDrawer() {
  $("app").classList.remove("nav-open");
  $("navbtn").setAttribute("aria-expanded", "false");
}
$("navbtn").onclick = openDrawer;
$("current").onclick = () => (narrow.matches ? openDrawer() : $("filter").focus());

$("scrim").onclick = closeDrawer;
$("drawerclose").onclick = closeDrawer;
// "/" filters the sessions, unless the keys go to a field or the terminal
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey || e.target.closest?.("input, textarea, [contenteditable]")) return;
  e.preventDefault();
  openDrawer();
  $("filter").focus();
});
$("navclose").onclick = () => {
  if (narrow.matches) return closeDrawer();
  $("app").classList.add("nav-hidden"); store.set("navHidden", true);
};
$("filter").addEventListener("input", drawNav);
$("filter").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("sessions").querySelector(".item")?.click();
  if (e.key === "Escape") { $("filter").value = ""; drawNav(); input.focus(); }
});
if (store.get("navHidden", false)) $("app").classList.add("nav-hidden");

// ---------- theme ----------

let shownTheme = "", shownFont = "";
function applyTheme(theme) {
  // sent with every subscribe and resume; the same one changes nothing (and rebuilds no line)
  const key = JSON.stringify(theme);
  if (key === shownTheme) return;
  shownTheme = key;
  views.setTheme(theme);
  const r = document.documentElement.style;
  r.setProperty("--t-bg", theme.bg); r.setProperty("--t-fg", theme.fg);
  r.setProperty("--accent", theme.cursor || theme.fg);
  if (theme.sel) r.setProperty("--sel", theme.sel);
  if (theme.selfg) r.setProperty("--selfg", theme.selfg);
  document.querySelector('meta[name="theme-color"]').content = theme.bg;
  if (st.size === null && theme.size) setSize(narrow.matches ? Math.min(theme.size, 13) : theme.size, false);
  if (theme.font && document.fonts && theme.font !== shownFont) {
    shownFont = theme.font;
    const face = new FontFace("TermFont", `local("${theme.font.replace(/["\\]/g, "")}")`);
    face.load().then((f) => document.fonts.add(f)).catch(() => { /* not on this device: Menlo */ });
  }
  view.setTheme(theme);
}

function setSize(px, save = true) {
  st.size = Math.max(7, Math.min(28, Math.round(px)));
  document.documentElement.style.setProperty("--size", `${st.size}px`);
  $("sizeval").textContent = st.size;
  if (save) store.set("size", st.size);
}

// ---------- view modes ----------
// Wrap: lines re-flow to the screen. Grid: iTerm's exact layout at the chosen text size.
// Fit: iTerm's exact layout scaled down so a whole line fits the screen width; iTerm itself is
// not touched. (Resizing iTerm to the screen is a separate menu action, as it changes the Mac.)

// The mode is remembered per session, and the one chosen last is the default for every other:
// session ids change when iTerm2 restarts or tmux -CC attaches again.
const MODES_KEPT = 100;
// Until the user picks one: Wrap, on every screen.
function mode() { return st.mode[st.sid] ?? store.get("modeDefault", "wrap"); }
function setMode(m, chosen = true) {
  delete st.mode[st.sid];                          // re-added last: the oldest choices go first
  st.mode[st.sid] = m;
  const ids = Object.keys(st.mode);
  for (const id of ids.slice(0, Math.max(0, ids.length - MODES_KEPT))) delete st.mode[id];
  store.set("mode", st.mode);
  if (chosen) store.set("modeDefault", m);
  view.keepBottom(syncControls);
}

function applyFit() {
  const term = $("term");
  if (mode() !== "fit" || !view.cols) { term.style.fontSize = ""; return; }
  const px = Math.floor(((view.inner().w - 1) / view.cols / view.charRatio()) * 10) / 10;
  term.style.fontSize = `${Math.min(px, st.size + 4)}px`;
}
view.onCols = () => view.keepBottom(applyFit);
document.fonts?.addEventListener("loadingdone", () => { view.ratio = 0; view.keepBottom(applyFit); });

function syncControls() {
  const m = mode();
  $("term").classList.toggle("wrap", m === "wrap");
  $("term").classList.toggle("grid", m !== "wrap");
  for (const b of document.querySelectorAll("[data-mode]")) b.setAttribute("aria-pressed", String(b.dataset.mode === m));
  $("resize").querySelector(".label").textContent = st.resized ? "Restore iTerm's size" : "Resize iTerm to this screen";
  $("appcur").checked = st.appCursor;
  $("bracket").checked = st.bracketed;
  $("showiterm").checked = st.showInIterm;
  const keysOn = !$("hotkeys").hidden;
  $("keysbtn").setAttribute("aria-pressed", String(keysOn));
  $("keysfab").setAttribute("aria-pressed", String(keysOn));
  document.documentElement.style.setProperty("--keys-h", `${$("hotkeys").offsetHeight}px`);
  applyFit();
}

for (const b of document.querySelectorAll("[data-mode]")) b.onclick = () => setMode(b.dataset.mode);
$("smaller").onclick = () => view.keepBottom(() => { setSize(st.size - 1); applyFit(); });
$("bigger").onclick = () => view.keepBottom(() => { setSize(st.size + 1); applyFit(); });
$("appcur").onchange = (e) => { st.appCursor = e.target.checked; };
$("bracket").onchange = (e) => { st.bracketed = e.target.checked; store.set("bracketed", st.bracketed); };
$("showiterm").onchange = (e) => {
  st.showInIterm = e.target.checked; store.set("showInIterm", st.showInIterm);
  send({ t: "prefs", showInIterm: st.showInIterm });
};
$("resize").onclick = () => {
  closeMenu();
  if (st.resized) return send({ t: "unfit" });
  if (mode() === "wrap") setMode("grid", false);   // a resized session is shown as its exact grid
  $("term").style.fontSize = "";
  send({ t: "fit", ...view.gridFit() });
};

// ---------- the on-screen keyboard ----------
// iOS Safari lays the keyboard over the page instead of shrinking it. Size the app to the
// visible part of the screen, so the terminal and the hot keys sit just above the keyboard,
// and give the space back when the keyboard closes.
const vv = window.visualViewport;
function fitViewport() {
  if (!vv) return;
  const stick = view.atBottom();
  document.documentElement.style.setProperty("--app-h", `${Math.round(vv.height)}px`);
  $("app").style.transform = vv.offsetTop ? `translateY(${Math.round(vv.offsetTop)}px)` : "";
  applyFit();
  if (stick) view.toBottom();
}
vv?.addEventListener("resize", fitViewport);
vv?.addEventListener("scroll", fitViewport);
addEventListener("resize", fitViewport);   // a window resize does not always reach visualViewport

function showKeys(on) {
  $("hotkeys").hidden = !on;
  store.set("keys", on);
  view.keepBottom(syncControls);
}
$("keysbtn").onclick = () => showKeys($("hotkeys").hidden);
$("keysfab").onclick = () => showKeys($("hotkeys").hidden);
$("keysfab").onmousedown = (e) => e.preventDefault();      // the iOS keyboard stays up
$("signout").onclick = async () => {
  closeMenu();
  const ws = st.ws; st.ws = null; ws?.close();
  await fetch("/auth/logout", { method: "POST" }).catch(() => {});
  showLogin();
};

function openMenu() { $("menu").hidden = false; $("menubtn").setAttribute("aria-expanded", "true"); }
function closeMenu() { $("menu").hidden = true; $("menubtn").setAttribute("aria-expanded", "false"); }
$("menubtn").onclick = (e) => { e.stopPropagation(); $("menu").hidden ? openMenu() : closeMenu(); };
document.addEventListener("click", (e) => { if (!$("menu").hidden && !e.target.closest("#menu")) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeMenu(); closeDrawer(); } });

// Toolbar buttons never take focus from the terminal: Enter must keep going to the shell.
$("bar").addEventListener("mousedown", (e) => { if (e.target.closest("#tools button")) e.preventDefault(); });

$("hotkeys").hidden = !store.get("keys", false);
if (st.size) setSize(st.size, false);
syncControls();
connect();
