// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The terminal view: scrollback + screen lines, kept in sync with incremental server messages.
import { inputBox, statusKey } from "./agentbox.js";
import { typedEnd } from "./cursor.js";
import { lineRec, palette } from "./render.js";

const LOAD_OLDER_AT = 300;   // px from the top that triggers loading older lines
const MAX_BLOCK = 400;       // most lines in a paragraph's block: a longer one is cut (one huge soft-wrapped line)
const BOXED = /^[│┃║╎┆|]|[│┃║╎┆|]$/; // rows of a drawn box: never re-joined

// Programs such as Claude Code wrap prose themselves at iTerm's width, with hard line ends.
// Re-flowing that on a narrow screen leaves ragged half-lines. A hard end is treated as a
// wrap (and re-joined in Wrap mode) when the next line's first word would not have fit:
// that is exactly the rule the program used to break the line.
function wrappedHere(a, b, cols) {
  if (!a || !b || !a.data.e || !cols) return false;
  if (a.el.classList.contains("rule") || b.el.classList.contains("rule")) return false;
  return wrapsInto(a.txt, b.txt, cols);
}

// Lines that start something new: a list item, an agent's mark (⏺ ⎿ ● ❯ ✻ · and other symbols
// and emoji) or a numbered line of a listing or a diff ("151:", "12 +", "694-}").
const STARTS_NEW = /^(?:[-*•+>]\s|\d+[.)]\s|[\u00b7\u2190-\u2bff\u{1f000}-\u{1faff}]|\d+(?::|\s*[-+]))/u;

/** True when line a's hard end is a wrap of the text that goes on in line b. */
export function wrapsInto(a, b, cols) {
  if (!a || !b.trim()) return false;
  if (BOXED.test(a.trim()) || BOXED.test(b.trim())) return false;
  const next = b.trimStart();
  if (STARTS_NEW.test(next)) return false;
  // Right-aligned or centered text is padded in front, which makes a short line look full.
  const indent = (t) => t.length - t.trimStart().length;
  if (indent(a) > cols / 4 || indent(b) > cols / 4) return false;
  const word = next.split(/\s/, 1)[0].length;
  return a.length + 1 + word > cols - 4;
}

/** Where blocks of lines end: after the first paragraph end (ends[i]) at least `min` lines
 *  into each block, or after `max` lines without one. Returns the end indexes (exclusive);
 *  lines after the last one are left. */
export function blockCuts(ends, min, max = Infinity) {
  const cuts = [];
  let start = 0;
  ends.forEach((end, i) => { if ((end && i + 1 - start >= min) || i + 1 - start >= max) cuts.push(start = i + 1); });
  return cuts;
}

const endsParagraph = (r) => r.el.classList.contains("eol") && !r.el.classList.contains("join");
// nothing drawn: no text, no background, no inverse cell
const blankRow = (r) => !r.txt.trim() && !r.data.r.some(([, , bg, flags]) => bg != null || flags & 32);

export class TermView {
  /** keep: the most history lines on the page while it follows the output (older ones load
   *  again on scrolling up). onRows(full) after the rows changed and onReset (set by the page):
   *  the output widgets (widgets.js). */
  constructor({ term, hist, screen, note, bottomBtn, paused, send, keep = Infinity }) {
    Object.assign(this, { term, histEl: hist, screenEl: screen, note, bottomBtn, paused, send, keep });
    this.theme = null; this.pal = []; this.cols = 0;
    this.reset();
    this.touching = false;
    term.addEventListener("scroll", () => this.onScroll(), { passive: true });
    bottomBtn.addEventListener("click", () => this.toBottom());
    // While a finger is down or text is selected, the page must not change under it: a
    // re-drawn line drops the selection (and iOS's long-press). Updates wait and then catch up.
    let lifting = 0;      // a lift's timer must not end the next touch
    term.addEventListener("touchstart", () => { clearTimeout(lifting); this.touching = true; }, { passive: true });
    const lift = (e) => {
      if (e.touches.length) return;                                // another finger is still down
      clearTimeout(lifting); lifting = setTimeout(() => { this.touching = false; this.resume(); }, 400);
    };
    term.addEventListener("touchend", lift, { passive: true });
    term.addEventListener("touchcancel", lift, { passive: true });
    document.addEventListener("selectionchange", () => this.resume());
  }

  reset() {
    this.hist = [];          // line records, oldest first, each with .n
    this.screen = [];        // line records
    this.oldest = 0; this.truncated = false; this.loading = false;
    this.queued = []; this.pending = null;
    this.histEl.replaceChildren(); this.screenEl.replaceChildren();
    this.note.hidden = true;
    this.onReset?.();
  }

  frozen() {
    if (this.touching) return true;
    const sel = getSelection();
    if (!sel || sel.isCollapsed) return false;
    // a selection in an output widget is in its row's shadow root (AC-56)
    for (let n = sel.anchorNode; n; n = n.getRootNode().host) if (this.term.contains(n)) return true;
    return false;
  }

  resume() {
    const frozen = this.frozen();
    const show = frozen && !!(this.queued.length || this.pending);
    if (show && this.paused.hidden) this.paused.style.top = `${this.term.offsetTop + 8}px`;   // under the header, whatever its height
    this.paused.hidden = !show;
    if (frozen) return;
    const q = this.queued; this.queued = [];
    for (const m of q) this.onHist(m);
    if (this.pending) this.flush();
  }

  setTheme(theme) {
    this.theme = theme; this.pal = palette(theme);
    this.keepBottom(() => {
      this.hist = this.hist.map((r) => this.swap(r, r.data, r.n));
      this.screen = this.screen.map((r) => this.swap(r, r.data));
      this.relinkAll();
      this.onRows?.(true);
    });
  }

  make(data, n) { const r = lineRec(data, this.theme, this.pal); r.n = n; return r; }
  swap(old, data, n) { const r = this.make(data, n); old.el.replaceWith(r.el); return r; }

  // Neighbors across the history/screen boundary, for re-joining.
  at(i) { return i < this.hist.length ? this.hist[i] : this.screen[i - this.hist.length]; }
  link(i) {
    const a = this.at(i), b = this.at(i + 1);
    if (!a) return;
    const join = wrappedHere(a, b, this.cols);
    a.el.classList.toggle("join", join);
    // a separator at least half of iTerm's width is a line across the whole view; a line pushed
    // to the right by spaces (a notice) loses them on a phone, where they would wrap it
    a.el.classList.toggle("full", a.el.classList.contains("rule") && !a.el.classList.contains("labelled") && a.txt.trim().length * 2 >= this.cols);
    a.el.classList.toggle("ralign", !!this.cols && a.txt.length >= this.cols - 2 && (a.txt.length - a.txt.trimStart().length) * 3 >= this.cols);
    if (b) b.el.classList.toggle("cont", join);
  }
  relinkAll() {
    const n = this.hist.length + this.screen.length;
    for (let i = 0; i < n; i++) this.link(i);
    const all = new DocumentFragment();                            // paragraphs may end elsewhere now
    for (const r of this.hist) all.append(r.el);                   // (no spread: 10000s of arguments)
    this.histEl.replaceChildren(all);
    this.pack();
    this.groupScreen();
  }

  // Each paragraph (lines up to a hard line end not re-joined) is a block of its own. In Wrap
  // the lines are inline, and iOS Safari 26.4+ shows no selection (no highlight, handles or
  // Copy) in a long run of them; older iOS read the whole run for its Copy menu and froze. The
  // paragraph the history's newest lines are in stays loose: it flows on into the screen, and
  // its last line's end can still change with the screen's first line.
  wrapInBlocks(rows) {
    let start = 0;
    for (const end of blockCuts(rows.map(endsParagraph), 1, MAX_BLOCK)) {
      this.block(rows.slice(start, end));
      start = end;
    }
    return rows.slice(start);
  }
  block(rows) {
    const b = document.createElement("div");
    b.className = "blk";
    rows[0].el.before(b);
    b.append(...rows.map((r) => r.el));
  }
  pack() {
    let loose = 0;
    while (loose < this.hist.length && this.hist.at(-1 - loose).el.parentNode === this.histEl) loose++;
    if (loose > 1) this.wrapInBlocks(this.hist.slice(-loose, -1));
  }
  // The screen's paragraphs in blocks too, but its first: that one goes on from the history's.
  // In a coding agent's pane its input box, with its rules, is one block ("inbox") and the
  // status lines below it another ("foot"): on a phone the page draws the box as a panel and
  // keeps the footer behind a button. The blocks are rebuilt only when they change.
  groupScreen() {
    const rows = this.screen.filter(Boolean);                     // a row not received yet is skipped
    // The screen's empty rows below its last text and the cursor are not shown: after a clear a
    // prompt stands at the top and the view, kept at its end, showed only them.
    let used = rows.findIndex((r) => r.el.querySelector(".cur"));
    for (let i = rows.length - 1; i > used; i--) if (!blankRow(rows[i])) { used = i; break; }
    rows.forEach((r, i) => r.el.classList.toggle("spare", i > used));
    const at = this.agent ? this.findBox(rows) : null;
    this.term.classList.toggle("hasbox", !!at);
    const groups = [{ kind: "", rows: [] }];
    rows.forEach((r, i) => {
      const kind = !at || i < at.top ? "" : i <= at.bottom ? "inbox" : i <= at.end ? "foot" : "";
      let g = groups.at(-1);
      if (g.rows.length && g.kind !== kind) groups.push(g = { kind, rows: [] });
      else if (!g.rows.length && groups.length > 1) g.kind = kind;
      else if (!g.rows.length && kind) groups.push(g = { kind, rows: [] });   // the first group stays loose
      g.rows.push(r);
      if (!kind && endsParagraph(r)) groups.push({ kind: "", rows: [] });
    });
    if (groups.length > 1 && !groups.at(-1).rows.length) groups.pop();
    const cls = (g) => (g.kind ? `blk ${g.kind}` : "blk");
    const box = this.screenEl, kept = groups.every((g, k) => {
      const p = g.rows[0]?.el.parentNode;
      return k === 0 ? g.rows.every((r) => r.el.parentNode === box)
        : p !== box && p?.parentNode === box && p.className === cls(g) && p.childElementCount === g.rows.length && g.rows.every((r) => r.el.parentNode === p);
    });
    if (kept && box.childElementCount === groups[0].rows.length + groups.length - 1) return;
    const all = new DocumentFragment();
    all.append(...groups[0].rows.map((r) => r.el));
    for (const g of groups.slice(1)) {
      const b = document.createElement("div");
      b.className = cls(g);
      b.append(...g.rows.map((r) => r.el));
      all.append(b);
    }
    box.replaceChildren(all);
  }
  // The agent's input box. A cursor gone for a frame (a redraw) keeps the box while its rules
  // stay. The status lines shown below it while nothing is typed are learnt: only those wait
  // behind the button, so a "/" or "@" list or a new notice there stays in sight.
  findBox(rows) {
    const cursor = rows.findIndex((r) => r.el.querySelector(".cur"));
    let at = inputBox(rows.map((r) => ({ rule: r.el.classList.contains("rule"), blank: !r.txt.trim() })), cursor);
    const was = this.box;
    if (!at && cursor < 0 && was && was.end < rows.length && rows[was.top]?.el.classList.contains("rule") && rows[was.bottom]?.el.classList.contains("rule")) at = was;
    this.box = at;
    if (!at) return null;
    const below = rows.slice(at.bottom + 1, at.end + 1);
    if (cursor >= 0 && typedEnd(rows[cursor].data) <= 2) this.idle = new Set(below.map((r) => statusKey(r.txt)));   // only the prompt (a faint suggestion is not typed)
    for (const r of below) r.el.classList.toggle("status", !!this.idle?.has(statusKey(r.txt)));
    return at;
  }
  /** The shown pane runs a coding agent (or no longer): its input box is drawn as a panel. */
  setAgent(on) {
    if (this.agent === on) return;
    this.agent = on;
    this.box = this.idle = null;
    this.keepBottom(() => this.groupScreen());
  }

  /** Typing: for 3 s, and at once, keep the cursor's row in sight (an agent's input box
   *  stands above its footer, so the screen's last row is not where the user types). */
  typed() { this.followUntil = performance.now() + 3000; this.showCursor(); }
  showCursor() {
    const cur = this.screenEl.querySelector(".cur");
    if (!cur) return this.toBottom();
    const t = this.term.getBoundingClientRect(), r = cur.getBoundingClientRect();
    const margin = 2 * (r.height || 16);
    if (r.bottom > t.bottom - margin || r.top < t.top) this.term.scrollTop += r.bottom - (t.bottom - margin);
    this.updateBottom();
  }

  atBottom() { return this.term.scrollHeight - this.term.scrollTop - this.term.clientHeight < 8; }
  toBottom() { this.term.scrollTop = this.term.scrollHeight; this.updateBottom(); }
  updateBottom() { this.bottomBtn.hidden = this.atBottom(); }
  keepBottom(fn) { const stick = this.atBottom(); fn(); if (stick) this.term.scrollTop = this.term.scrollHeight; this.updateBottom(); }

  onHist(m) {
    if (!this.theme) return;
    if (this.frozen() && m.mode !== "reset") { this.queued.push(m); this.resume(); return; }
    const rows = m.lines.map((data, i) => this.make(data, m.first + i));
    this.oldest = m.oldest;
    this.truncated = m.truncated;
    if (m.mode === "reset") {
      this.hist = rows;
      this.keepBottom(() => { this.relinkAll(); this.onRows?.(true); });   // it puts the lines in place
    } else if (m.mode === "append") {
      const known = this.hist.length ? this.hist.at(-1).n : -1;
      const fresh = rows.filter((r) => r.n > known);
      const from = this.hist.length - 1;
      this.hist.push(...fresh);
      // the cap applies while the user follows the output: not while an older page is on its
      // way (it would leave a gap), nor behind Files or File (a hidden view is "at the bottom")
      const follow = !this.loading && !this.term.hidden && this.atBottom();
      this.keepBottom(() => {
        this.histEl.append(...fresh.map((r) => r.el));
        for (let i = Math.max(0, from); i <= this.hist.length; i++) this.link(i);
        this.trim(follow);
        this.pack();
        this.groupScreen();
        this.onRows?.(false);
      });
    } else {                                        // prepend older lines, keep the view still
      const first = this.hist.length ? this.hist[0].n : Infinity;
      const older = rows.filter((r) => r.n < first);
      this.loading = false;
      if (older.length && first !== Infinity && older.at(-1).n !== first - 1) return;   // a gap: scrolling asks again
      const h = this.term.scrollHeight;
      this.hist.unshift(...older);
      this.histEl.prepend(...older.map((r) => r.el));
      for (let i = 0; i <= older.length; i++) this.link(i);
      const last = this.hist.length === older.length ? 1 : 0;         // the last line stays loose (pack)
      const rest = older.length > last ? this.wrapInBlocks(older.slice(0, older.length - last)) : [];
      const next = rest.at(-1)?.el.nextElementSibling;              // their paragraph goes on there
      if (next?.classList.contains("blk")) {
        if (rest.length + next.childElementCount <= MAX_BLOCK) next.prepend(...rest.map((r) => r.el));
        else this.block(rest);                                      // cut, as a longer paragraph is
      }
      this.onRows?.(true);
      this.term.scrollTop += this.term.scrollHeight - h;
    }
    this.showNote();
  }

  // Drop lines older than the server's limit, so a long-running session stays light; while
  // following the output, also whole blocks beyond `keep` lines.
  trim(follow) {
    let drop = 0;
    while (drop < this.hist.length && this.hist[drop].n < this.oldest) drop++;
    for (const l of this.hist.slice(0, drop)) l.el.remove();
    this.hist = this.hist.slice(drop);
    let cut = 0;
    for (let b = this.histEl.firstElementChild; b?.classList.contains("blk"); b = this.histEl.firstElementChild) {
      const n = b.childElementCount;
      if (n && !(follow && this.hist.length - cut - n >= this.keep)) break;
      b.remove();
      cut += n;
    }
    this.hist = this.hist.slice(cut);
  }

  showNote() {
    const all = !this.hist.length || this.hist[0].n <= this.oldest;
    this.note.hidden = !(all && this.truncated);
  }

  onScreen(m) {
    // Coalesce bursts: apply the latest state once per animation frame.
    if (!this.pending || m.full) this.pending = { full: m.full, n: m.n, ch: new Map() };
    this.pending.n = m.n;
    if (m.cols !== this.cols) { this.cols = m.cols; this.onCols?.(); }
    for (const [i, data] of m.ch) this.pending.ch.set(i, data);
    if (!this.raf) this.raf = requestAnimationFrame(() => this.flush());
  }

  flush() {
    this.raf = null;
    if (this.frozen()) { this.resume(); return; }
    const p = this.pending; this.pending = null;
    if (!p || !this.theme) return;
    this.keepBottom(() => {
      if (p.full) { this.screen = []; this.screenEl.replaceChildren(); }
      while (this.screen.length > p.n) this.screen.pop().el.remove();
      const changed = [...p.ch].sort((a, b) => a[0] - b[0]);
      for (const [i, data] of changed) {
        if (this.screen[i]) this.screen[i] = this.swap(this.screen[i], data);
        else { this.screen[i] = this.make(data); this.screenEl.append(this.screen[i].el); }
      }
      if (p.full) this.relinkAll();
      else {
        for (const [i] of changed) { this.link(this.hist.length + i - 1); this.link(this.hist.length + i); }
        this.groupScreen();
      }
      this.onRows?.(false);
    });
    if (performance.now() < (this.followUntil ?? 0)) this.showCursor();
    this.onDrawn?.();
  }

  onScroll() {
    this.updateBottom();
    const first = this.hist.length ? this.hist[0].n : null;
    if (this.term.scrollTop < LOAD_OLDER_AT && !this.loading && first !== null && first > this.oldest) {
      this.loading = true;
      this.send({ t: "more", before: first });
    }
  }

  /** The text area's inner size in px. */
  inner() {
    const cs = getComputedStyle(this.term);
    return { cs, w: this.term.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
      h: this.term.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) };
  }

  /** One cell's advance per px of font size, for the terminal font now loaded. */
  charRatio() {
    if (this.ratio) return this.ratio;
    const probe = document.createElement("span");
    probe.textContent = "0".repeat(100);
    probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre;font-size:100px";
    this.term.append(probe);
    this.ratio = probe.getBoundingClientRect().width / 100 / 100;
    probe.remove();
    return this.ratio;
  }

  /** Columns and rows that fit the visible area at the current font size. */
  gridFit() {
    const { cs, w, h } = this.inner();
    return { cols: Math.floor(w / (this.charRatio() * parseFloat(cs.fontSize))), rows: Math.floor(h / parseFloat(cs.lineHeight)) };
  }
}
