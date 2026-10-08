// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The terminal view: scrollback + screen lines, kept in sync with incremental server messages.
import { lineRec, palette } from "./render.js";

const LOAD_OLDER_AT = 300;   // px from the top that triggers loading older lines
const BLOCK = 100;           // fewest history lines in a block (see pack)
const MAX_BLOCK = 4 * BLOCK; // most: a longer paragraph is cut (one huge soft-wrapped line)
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

export class TermView {
  /** keep: the most history lines on the page while it follows the output (older ones load
   *  again on scrolling up). */
  constructor({ term, hist, screen, note, bottomBtn, paused, send, keep = Infinity }) {
    Object.assign(this, { term, histEl: hist, screenEl: screen, note, bottomBtn, paused, send, keep });
    this.theme = null; this.pal = []; this.cols = 0;
    this.reset();
    this.touching = false;
    term.addEventListener("scroll", () => this.onScroll(), { passive: true });
    bottomBtn.addEventListener("click", () => this.toBottom());
    // While a finger is down or text is selected, the page must not change under it: a
    // re-drawn line drops the selection (and iOS's long-press). Updates wait and then catch up.
    term.addEventListener("touchstart", () => { this.touching = true; }, { passive: true });
    const lift = () => { setTimeout(() => { this.touching = false; this.resume(); }, 400); };
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
  }

  frozen() {
    if (this.touching) return true;
    const sel = getSelection();
    return !!sel && !sel.isCollapsed && this.term.contains(sel.anchorNode);
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
    if (b) b.el.classList.toggle("cont", join);
  }
  relinkAll() {
    const n = this.hist.length + this.screen.length;
    for (let i = 0; i < n; i++) this.link(i);
    const all = new DocumentFragment();                            // paragraphs may end elsewhere now
    for (const r of this.hist) all.append(r.el);                   // (no spread: 10000s of arguments)
    this.histEl.replaceChildren(all);
    this.pack();
  }

  // iOS reads the whole block a selection lies in for its Copy menu, and in Wrap all lines flow
  // in one: with thousands of lines a tap on a selection froze the page for seconds. So the
  // history is kept in blocks that end where a paragraph ends (a hard line end not re-joined).
  // The newest lines stay loose: they flow on into the screen, and the last one's end can still
  // change with the screen's first line.
  wrapInBlocks(rows) {
    let start = 0;
    for (const end of blockCuts(rows.map(endsParagraph), BLOCK, MAX_BLOCK)) {
      const b = document.createElement("div");
      b.className = "blk";
      rows[start].el.before(b);
      b.append(...rows.slice(start, end).map((r) => r.el));
      start = end;
    }
    return rows.slice(start);
  }
  pack() {
    let loose = 0;
    while (loose < this.hist.length && this.hist.at(-1 - loose).el.parentNode === this.histEl) loose++;
    if (loose > BLOCK) this.wrapInBlocks(this.hist.slice(-loose, -1));
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
      this.keepBottom(() => this.relinkAll());                   // it puts the lines in place
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
      if (next?.classList.contains("blk")) next.prepend(...rest.map((r) => r.el));
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
    for (let b = this.histEl.firstElementChild; b?.classList.contains("blk"); b = this.histEl.firstElementChild) {
      const n = b.childElementCount;
      if (n && !(follow && this.hist.length - n >= this.keep)) break;
      b.remove();
      this.hist = this.hist.slice(n);
    }
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
      else for (const [i] of changed) { this.link(this.hist.length + i - 1); this.link(this.hist.length + i); }
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
