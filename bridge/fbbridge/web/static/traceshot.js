// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The context trace's snapshots (AC-55, tracebuf.js; the format: docs/web-trace.md): the page's
// state as text lines: sizes and places, the session, its history and bookmarks, iTerm2's rows
// (a coding agent's input box and the screen) with the page's own row classes, and the lines
// the browser lays the input out in. Read-only: nothing on the page changes.

const HIST_ROWS = 12;          // the history's last rows in a snapshot
const SCREEN_ROWS = 120;       // the screen's rows at most
const WEB_CHARS = 3000;        // characters measured for the page's lines of the input at most
const PLACES = ["bar", "term", "hist", "screen", "kbd", "hotkeys", "fabs", "bottom", "markline", "markbar", "findbox", "toast", "status", "nav", "filewin"];

const round = (v) => Math.round(v * 10) / 10;
export const rectOf = (el) => { const r = el.getBoundingClientRect(); return [round(r.left), round(r.top), round(r.width), round(r.height)]; };
const json = (o) => JSON.stringify(o);

/** A short name of an element: its id, or its tag and classes and the nearest id above it. */
export function desc(el) {
  if (!el || el.nodeType !== 1) return el === document ? "document" : el === window ? "window" : String(el?.nodeName ?? "");
  if (el.id) return `#${el.id}`;
  const own = el.tagName.toLowerCase() + [...el.classList].slice(0, 3).map((c) => `.${c}`).join("");
  const up = el.parentElement?.closest("[id]");
  return up ? `${own}@#${up.id}` : own;
}

/** A row's text as iTerm2 has it: its runs joined (a wide character's right half is ""). */
export function plain(data) {
  return (data?.r ?? []).map(([t]) => (Array.isArray(t) ? t.join("") : t)).join("");
}

/** A row's runs that carry flags (render.js: 1 bold, 2 faint, 32 inverse, 256 the cursor...; 4096
 *  added here: a background color) as "start+length*flags", by characters of plain(). */
export function runs(data) {
  const out = [];
  let at = 0;
  for (const [t, , bg, flags] of data?.r ?? []) {
    const n = Array.isArray(t) ? t.join("").length : t.length, f = (flags || 0) | (bg != null ? 4096 : 0);
    if (f && n) out.push(`${at}+${n}*${f}`);
    at += n;
  }
  return out.join(",");
}

/** A row record's line: "|i flags runs|text". Flags: e a hard line end, j re-joined with the
 *  next row, g joined without a space, c goes on from the row before, r a rule, s a blank row
 *  not shown, p the cursor drawn here by the page (parked), w in a widget's region, h hidden. */
export function rowLine(i, rec) {
  const c = rec.el.classList, data = rec.orig ?? rec.data;
  const flags = [["e", data.e], ["j", c.contains("join")], ["g", c.contains("glue")], ["c", c.contains("cont")], ["r", c.contains("rule")],
    ["s", c.contains("spare")], ["p", !!rec.orig], ["w", c.contains("wg-host") || c.contains("wg-hid")], ["h", !rec.el.getClientRects().length]]
    .filter(([, on]) => on).map(([f]) => f).join("") || "-";
  const r = runs(data), text = plain(data).replace(/\s+$/, "");
  return `|${i} ${flags}${r ? ` ${r}` : ""}|${text}`;
}

/** The lines the browser lays el's text out in, as shown: [{x, text}] by the characters' boxes,
 *  with the space CSS draws after a re-joined row (style.css .join::after); cur: [line, column]
 *  of the cursor's cell. Characters past WEB_CHARS are left out. */
export function webLines(el) {
  const lines = [];
  let cur = null, line = null, count = 0;
  const range = document.createRange(), base = el.getBoundingClientRect();
  for (const row of el.querySelectorAll(".ln")) {
    const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    for (let t = walk.nextNode(); t && count < WEB_CHARS; t = walk.nextNode()) {
      const isCur = !!t.parentElement.closest(".cur");
      for (let i = 0; i < t.length && count < WEB_CHARS; i++, count++) {
        range.setStart(t, i); range.setEnd(t, i + 1);
        const r = range.getClientRects()[0];
        if (!r) continue;                                        // not shown (display: none)
        if (!line || r.top >= line.top + line.h / 2) lines.push(line = { top: r.top, h: r.height || 1, x: round(r.left - base.left), text: "" });
        if (isCur && !cur) cur = [lines.length - 1, line.text.length];
        line.text += t.data[i];
      }
    }
    const after = /^"(.*)"$/.exec(getComputedStyle(row, "::after").content)?.[1]
      ?.replace(/\\([0-9a-f]{1,6}) ?/gi, (_, h) => String.fromCodePoint(parseInt(h, 16))).replace(/\n/g, "");   // a CSS escape; a break shows as the next line
    if (after && line && row.getClientRects().length) line.text += after;
  }
  return { lines: lines.map(({ x, text }) => ({ x, text: text.replace(/[\ufe0e\ufe0f]/g, "") })), cur, cut: count >= WEB_CHARS };
}

/** The page's sizes: window, visual viewport, the terminal and its cells, iTerm2's grid. */
export function sizes(view) {
  const vv = window.visualViewport, term = view.term, cs = getComputedStyle(term), px = parseFloat(cs.fontSize);
  return { win: [innerWidth, innerHeight], vv: vv ? [round(vv.width), round(vv.height), round(vv.offsetTop)] : null, dpr: devicePixelRatio,
    term: [term.clientWidth, term.clientHeight], font: px, lh: parseFloat(cs.lineHeight) || null, cell: Math.round(view.charRatio() * px * 100) / 100,
    fits: view.gridFit(), iterm: [view.cols, view.screen.length], wrap: term.classList.contains("wrap"), alt: !!view.alt,
    scroll: [Math.round(term.scrollTop), term.scrollHeight, view.atBottom()] };
}

/** Where the page's parts are: [x, y, w, h] of those shown. */
export function places() {
  const out = {};
  for (const id of PLACES) {
    const el = document.getElementById(id);
    if (el && !el.hidden && el.getClientRects().length) out[id] = rectOf(el);
  }
  for (const [name, sel] of [["inbox", "#screen .blk.inbox"], ["foot", "#screen .blk.foot"], ["cur", "#screen .cur"]]) {
    const el = document.querySelector(sel);
    if (el?.getClientRects().length) out[name] = rectOf(el);
  }
  out.focus = desc(document.activeElement);
  return out;
}

// the input box's rows: [i, record], i < 0 in the history (an input taller than the screen)
function boxRows(view) {
  const box = view.box, rows = [];
  for (let i = box?.top ?? 0; box && i <= box.bottom; i++) {
    const rec = i < 0 ? view.hist.at(i) : view.screen[i];
    if (rec) rows.push([i, rec]);
  }
  return rows;
}

/** What the input box's snapshot depends on: "" without one. */
export function boxSig(view) {
  if (!view.box || !view.screenEl.querySelector(".blk.inbox")) return "";
  return json([view.cols, view.term.clientWidth, getComputedStyle(view.term).fontSize, view.term.className,
    boxRows(view).map(([, r]) => [r.el.className, plain(r.orig ?? r.data), runs(r.orig ?? r.data)])]);
}

/** A coding agent's input box as lines: iTerm2's rows and the page's lines of them ([] without one). */
export function boxShot(view) {
  const box = view.box, el = view.screenEl.querySelector(".blk.inbox");
  if (!box || !el) return [];
  const rows = boxRows(view), t0 = performance.now(), web = webLines(el), took = Math.round(performance.now() - t0);
  return [`box ${json({ top: box.top, bottom: box.bottom, end: box.end, caret: view.caret ? [view.caret.row, view.caret.col] : null, rows: rows.length })}`,
    ...rows.map(([i, r]) => rowLine(i, r)),
    `web ${json({ at: rectOf(el), lines: web.lines.length, cur: web.cur, cut: web.cut || undefined, ms: took })}`,
    ...web.lines.map((l, k) => `:${k} ${l.x}|${l.text}`)];
}

/** The page's state now, as lines (the copy's end). ctx: {view, st, bookmarks, widgets, mode(), findItem}. */
export function shot(ctx) {
  const { view, st, bookmarks, widgets } = ctx, lines = [];
  const it = st.sid ? ctx.findItem(st.groups, st.sid)?.item : null;
  lines.push(`env ${json({ ua: navigator.userAgent, lang: navigator.language, url: location.pathname + location.search,
    touch: matchMedia("(hover: none)").matches, fine: matchMedia("(pointer: fine)").matches, build: document.querySelector('meta[name="build"]')?.content,
    hidden: document.hidden, focus: document.hasFocus() })}`);
  lines.push(`size ${json({ ...sizes(view), mode: ctx.mode() })}`);
  lines.push(`places ${json(places())}`);
  lines.push(`sess ${json({ sid: st.sid, title: it?.title, agent: it?.agent ?? null, shell: it?.shell, state: it?.state, host: it?.host ?? null,
    sessions: st.groups.reduce((n, g) => n + g.items.length, 0), appCursor: st.appCursor, bracketed: st.bracketed })}`);
  const h = view.hist;
  lines.push(`hist ${json({ lines: h.length, first: h[0]?.n ?? null, last: h.at(-1)?.n ?? null, top: view.top, oldest: view.oldest, truncated: view.truncated,
    loading: view.loading, dom: view.term.querySelectorAll(".ln").length, blocks: view.term.querySelectorAll(".blk").length,
    widgets: widgets.hosts.size, folds: widgets.folds.items.size, open: widgets.folds.open.size })}`);
  const mine = bookmarks.mine(), jump = bookmarks.jumper, at = jump.at;
  const marked = at instanceof Range ? String(at) : at?.textContent ?? null;
  lines.push(`marks ${json({ all: bookmarks.items.size, here: mine.length, shown: bookmarks.shown?.id ?? null,
    items: mine.slice(0, 20).map((i) => ({ id: i.id, n: i.n, len: i.text.length, text: i.text.slice(0, 40) })),
    line: document.getElementById("markline")?.hidden === false ? rectOf(document.getElementById("markline")) : null,
    marked: marked == null ? null : { len: marked.length, head: marked.slice(0, 40), tail: marked.slice(-40) } })}`);
  lines.push(...boxShot(view));
  lines.push(`tail ${json({ rows: Math.min(HIST_ROWS, h.length) })}`);
  h.slice(-HIST_ROWS).forEach((r, k) => lines.push(rowLine(k - Math.min(HIST_ROWS, h.length), r)));
  const scr = view.screen.slice(0, SCREEN_ROWS);
  lines.push(`screen ${json({ cols: view.cols, rows: view.screen.length, top: view.top, alt: !!view.alt, box: view.box ?? null })}`);
  scr.forEach((r, i) => { if (r) lines.push(rowLine(i, r)); });
  return lines;
}
