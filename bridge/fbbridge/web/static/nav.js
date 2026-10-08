// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Session navigator: iTerm2 windows (or tmux sessions) as groups, their tabs and panes as rows.
// A row: the profile's dot (ringed when the session is focused in iTerm2), the tab number and
// title, then host · program · folder, and a coding agent's state in a column of its own.
import { icon, profileColor } from "./icons.js";
import { textForm } from "./render.js";

// A coding agent's state: working, waiting for the user, failed, done.
const STATES = {
  working: { label: "Working", icon: "hourglass" },
  waiting: { label: "Waiting for your answer", icon: "circle-help" },
  failed: { label: "Failed", icon: "triangle-alert" },
  done: { label: "Done", icon: "circle-check" },
};
export function stateIcon(state, size = 16) {
  const s = STATES[state];
  if (!s) return null;
  const wrap = document.createElement("span");
  wrap.className = `state ${state}`;
  wrap.title = s.label;
  wrap.setAttribute("role", "img");
  wrap.setAttribute("aria-label", s.label);
  wrap.append(icon(s.icon, size));
  return wrap;
}

/** The profile's dot; ringed when the session is the one focused in iTerm2, pulsing while a
 *  coding agent in it works. */
export function profileDot(profile, focused = false, busy = false) {
  const d = el("span", "pdot" + (focused ? " focus" : "") + (busy ? " busy" : ""));
  d.style.setProperty("--dot", profileColor(profile));
  d.title = (profile ? `Profile ${profile}` : "Profile unknown") + (focused ? ", focused in iTerm2" : "");
  return d;
}

const AGENT_WORDS = { claude: "ag-claude", codex: "ag-codex" };   // the program's own name, in its color

/** "a · b · c" as spans; the last part (a folder) is the one that shortens. */
export function metaLine(parts, hostFirst) {
  const s = el("span", "s");
  parts.forEach((p, i) => {
    if (i) s.append(el("span", "sep", "·"));
    const cls = i === 0 && hostFirst ? "host" : i === parts.length - 1 ? "last" : "";
    s.append(el("span", [cls, AGENT_WORDS[p]].filter(Boolean).join(" "), p));
  });
  return s;
}

/** The key a group's collapsed state is remembered by: its window or tmux session, and host. */
const groupKey = (g) => `${g.kind}\n${g.label}${g.session ? " " + g.session : ""}\n${g.host ?? ""}`;

function matches(item, q) {
  if (!q) return true;
  return [item.title, item.host ?? "", ...item.sub].join(" ").toLowerCase().includes(q);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = textForm(String(text));
  return e;
}

// The handle a row or a group is dragged by (reorder.js); not while a filter shows a subset.
function grip(kind, label) {
  const g = el("span", "grip");
  g.dataset.kind = kind;
  g.title = `Drag to reorder ${label}`;
  g.setAttribute("aria-hidden", "true");
  g.append(icon("grip-vertical", 14));
  return g;
}

function row(item, current, onPick, movable) {
  const li = document.createElement("li");
  if (movable) li.append(grip("row", "this tab"));
  const b = el("button", "item" + (item.id === current ? " on" : ""));
  b.type = "button";
  b.dataset.id = item.id;
  if (item.id === current) b.setAttribute("aria-current", "true");
  const txt = el("span", "txt");
  const head = el("span", "head");
  const idx = el("span", "idx", item.index || "");
  if (item.index) idx.title = `Tab ${item.index}`;
  head.append(idx, el("span", "t", item.title));
  txt.append(head);
  const parts = [...(item.host ? [item.host] : []), ...item.sub];
  if (parts.length) txt.append(metaLine(parts, !!item.host));
  const st = el("span", "st");
  const mark = stateIcon(item.state);
  if (mark) st.append(mark);
  b.append(profileDot(item.profile, item.focused, item.state === "working"), txt, st);
  b.addEventListener("click", () => onPick(item.id));
  li.append(b);
  return li;
}

// A group's header collapses and expands it; while a filter is typed every match is shown.
export function renderNav(list, groups, current, query, onPick, collapsed, onToggle, onNew) {
  const q = query.trim().toLowerCase();
  const frag = document.createDocumentFragment();
  let shown = 0;
  for (const g of groups) {
    const items = g.items.filter((it) => matches(it, q));
    if (!items.length) continue;
    shown += items.length;
    const key = groupKey(g);
    const closed = !q && collapsed.has(key);
    const sec = el("section", `grp ${g.kind}`);
    if (g.wid) sec.dataset.wid = g.wid;
    const h = el("h2");
    if (g.wid && !q) h.append(grip("group", "this window"));
    const t = el("button", "toggle");
    t.type = "button";
    t.setAttribute("aria-expanded", String(!closed));
    t.addEventListener("click", () => onToggle(key));
    t.append(icon("chevron-down", 14), el("span", "label", g.label));
    if (g.session) t.append(el("span", "tsess", g.session));      // a tmux group: its session's name; the rows name the host
    else if (g.host) t.append(el("span", "host", g.host));
    if (g.where) t.append(el("span", "where", g.where));
    const count = el("span", "count", String(g.items.length));
    count.title = `${g.items.length} session${g.items.length === 1 ? "" : "s"}`;
    if (closed && items.some((it) => it.id === current)) count.classList.add("on");   // the shown session is inside
    t.append(count);
    h.append(t);
    if (g.wid && g.new) {
      const n = el("button", "new");
      n.type = "button";
      n.title = g.new;
      n.setAttribute("aria-label", `${g.new} in ${g.label}`);
      n.append(icon("plus", 14));
      n.addEventListener("click", () => onNew(g.wid));
      h.append(n);
    }
    sec.append(h);
    if (!closed) {
      const ul = el("ul");
      if (g.wid) ul.dataset.wid = g.wid;
      for (const it of items) ul.append(row(it, current, onPick, !!g.wid && !q));
      sec.append(ul);
    }
    frag.append(sec);
  }
  if (!shown) {
    frag.append(el("p", "empty", q ? "No session matches. Clear the filter to see all." : "No iTerm2 sessions are open."));
  }
  list.replaceChildren(frag);
}

export function findItem(groups, id) {
  for (const g of groups) for (const it of g.items) if (it.id === id) return { group: g, item: it };
  return null;
}
