// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Session navigator: iTerm2 windows (or tmux sessions) as groups, their tabs and panes as rows.
import { textForm } from "./render.js";

const SVG = "http://www.w3.org/2000/svg";
const SHAPES = {
  // a laptop: this Mac
  local: ["M4 5.2C4 4.5 4.5 4 5.2 4h9.6c.7 0 1.2.5 1.2 1.2V12H4z", "M2 14.5h16"],
  // two stacked servers: a remote host
  remote: ["M4 3.5h12v5H4z", "M4 11.5h12v5H4z", "M6.8 6h.01", "M6.8 14h.01"],
};

// The kind badge: blue laptop for a session on this Mac, violet server for a remote one.
export function kindIcon(host) {
  const kind = host ? "remote" : "local";
  const wrap = document.createElement("span");
  wrap.className = `kind ${kind}`;
  wrap.title = host ? `On ${host}` : "On this Mac";
  wrap.append(icon(SHAPES[kind]));
  return wrap;
}

function icon(paths) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("aria-hidden", "true");
  for (const d of paths) {
    const p = document.createElementNS(SVG, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

/** The key a group's collapsed state is remembered by: its window or tmux session, and host. */
const groupKey = (g) => `${g.kind}\n${g.label}\n${g.host ?? ""}`;

function matches(item, q) {
  if (!q) return true;
  return [item.title, item.host ?? "", ...item.sub].join(" ").toLowerCase().includes(q);
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = textForm(text);
  return e;
}

function row(item, current, onPick) {
  const li = document.createElement("li");
  const b = el("button", "item" + (item.id === current ? " on" : ""));
  b.type = "button";
  b.dataset.id = item.id;
  if (item.id === current) b.setAttribute("aria-current", "true");
  const txt = el("span", "txt");
  const head = el("span", "head");
  if (item.index) { const idx = el("span", "idx", item.index); idx.title = `Tab ${item.index}`; head.append(idx); }
  head.append(el("span", "t", item.title));
  txt.append(head);
  if (item.host || item.sub.length) {
    const s = el("span", "s");
    if (item.host) s.append(el("span", "host", item.host));
    for (const part of item.sub) s.append(el("span", "", part));
    txt.append(s);
  }
  b.append(kindIcon(item.host), txt);
  if (item.focused) {
    const dot = el("span", "focus");
    dot.title = "Focused in iTerm2";
    dot.setAttribute("aria-label", "Focused in iTerm2");
    b.append(dot);
  }
  b.addEventListener("click", () => onPick(item.id));
  li.append(b);
  return li;
}

// A group's header collapses and expands it; while a filter is typed every match is shown.
export function renderNav(list, groups, current, query, onPick, collapsed, onToggle) {
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
    const h = el("h2");
    const t = el("button", "toggle");
    t.type = "button";
    t.setAttribute("aria-expanded", String(!closed));
    t.addEventListener("click", () => onToggle(key));
    t.append(icon(["M7 8l3 3 3-3"]), el("span", "label", g.label));
    if (g.host) t.append(el("span", "host", g.host));
    else if (g.where) t.append(el("span", "where", g.where));
    const count = el("span", "count", String(g.items.length));
    count.title = `${g.items.length} session${g.items.length === 1 ? "" : "s"}`;
    if (closed && items.some((it) => it.id === current)) count.classList.add("on");   // the shown session is inside
    t.append(count);
    h.append(t);
    sec.append(h);
    if (!closed) {
      const ul = el("ul");
      for (const it of items) ul.append(row(it, current, onPick));
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

