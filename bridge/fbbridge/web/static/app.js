// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
import { Input } from "./input.js";
import { findItem, kindIcon, renderNav } from "./nav.js";
import { textForm } from "./render.js";
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

const view = new TermView({ term: $("term"), hist: $("hist"), screen: $("screen"), note: $("note"),
  bottomBtn: $("bottom"), paused: $("paused"), send });
const input = new Input({ kbd: $("kbd"), kbdButton: $("kbdbtn"), term: $("term"), panel: $("hotkeys"), pasteDialog: $("pastebox"),
  send: (data) => { send({ t: "in", data }); if (data.includes("\r")) views.commandSent(); },
  options: { appCursor: () => st.appCursor, bracketed: () => st.bracketed, onTyped: () => view.toBottom(), status: toast } });

const views = new Views({ send, store, onShow: () => view.keepBottom(applyFit) });

let toastTimer = 0;
function toast(text) {
  $("toast").textContent = text; $("toast").hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { $("toast").hidden = true; }, 1800);
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

function signedIn() {
  $("login").hidden = true; $("app").hidden = false;
  send({ t: "prefs", showInIterm: st.showInIterm });
  if (st.sid) select(st.sid, true);
}

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
  }
}

// ---------- navigation ----------

function drawNav() {
  renderNav($("sessions"), st.groups, st.sid, $("filter").value, (id) => { select(id); closeDrawer(); }, st.collapsed, toggleGroup);
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

function showCurrent(found) {
  $("curkind").replaceChildren(...(found ? [kindIcon(found.item.host)] : []));
  $("current").querySelector(".t").textContent = found ? textForm(found.item.title) : "Choose a session";
  const parts = found ? [found.item.host ?? found.group.label, ...found.item.sub] : [];
  $("current").querySelector(".s").replaceChildren(...parts.map((p, i) => {
    const s = document.createElement("span"); s.textContent = textForm(p);
    if (i === 0 && found.item.host) s.className = "host";
    return s;
  }));
  document.title = found ? `${found.item.title} – iTerm2 Web` : "iTerm2 Web";
}

function select(id, force = false) {
  if (id === st.sid && !force && view.screen.length) return;
  st.sid = id; store.set("sid", id);
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
function mode() { return st.mode[st.sid] ?? store.get("modeDefault", touch.matches ? "wrap" : "grid"); }
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
