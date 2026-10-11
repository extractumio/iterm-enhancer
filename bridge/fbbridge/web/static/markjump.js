// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The way back to a bookmark (AC-57). The bridge finds its text nearest the line it was on
// (iTerm2 numbers lines anew when it wraps them again); the page then loads older lines down to
// it, opens a fold it is in, finds the text in the rows or in a widget's rendered text, puts it
// a third from the terminal's top and marks it with a wavy line on its left. The widgets keep it there while they are drawn
// around it (widgets.js pin) until the user scrolls, taps, clicks or types.
import { loose, part, rangeOf, textNodes } from "./marktext.js";

const WAIT = 15000;            // ms for the bridge's answer
const CONTEXT = 40;            // lines loaded above the bookmark
const STEPS = 30;              // older pages and folds opened, at most
const SPAN = 400;              // rows its text is looked for in, from its line

export class Jumper {
  /** view: the TermView; widgets: the Widgets (its pin and folds); send(msg) to the bridge;
   *  sid(): the pane shown. */
  constructor({ view, widgets, term, send, sid }) {
    Object.assign(this, { view, widgets, term, send, sid });
    this.waiters = new Set();
    this.token = 0;
    this.at = null;              // what is marked: a Range, or an element when no text range was found
    // the line goes with the text when the page moves under it (a widget drawn, the view scrolled)
    // (it lies in the scrolled content: only content changing size above it, or a sideways
    // scroll, moves the text from under it; the text is looked up again only once its range went)
    let frame = 0, left = 0;
    this.follow = () => {
      if (frame || !this.find) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        if (!this.find) return;
        const at = this.at, live = at instanceof Range ? at.startContainer.isConnected : at?.isConnected;
        this.mark(live ? at : this.find());
      });
    };
    term.addEventListener("scroll", () => { if (term.scrollLeft !== left) { left = term.scrollLeft; this.follow(); } }, { passive: true });
    const sizes = new ResizeObserver(this.follow);
    for (const el of [term, ...term.querySelectorAll("#hist, #screen")]) sizes.observe(el);
    // the user moves the view: scrolls, taps, clicks, types (also by the on-screen keyboard), or
    // goes to the end
    const release = () => { this.widgets.pin = null; };
    for (const e of ["wheel", "touchstart", "pointerdown"]) term.addEventListener(e, release, { passive: true });
    document.addEventListener("keydown", release);
    document.getElementById("kbd")?.addEventListener("input", release);
    document.getElementById("bottom")?.addEventListener("click", release);
  }

  /** Every message from the bridge (app.js): answers someone may wait for. */
  seen(m) { for (const w of [...this.waiters]) if (w.test(m)) { this.waiters.delete(w); w.resolve(m); } }

  /** The next message that passes test; rejects after ms. */
  wait(test, ms = WAIT) {
    return new Promise((resolve, reject) => {
      const w = { test, resolve: (m) => { clearTimeout(timer); resolve(m); } };
      const timer = setTimeout(() => { this.waiters.delete(w); reject(new Error("The Mac did not answer within 15 s.")); }, ms);
      this.waiters.add(w);
    });
  }

  // the page's updates wait while a finger is down or text is selected: until they are in
  async settled() { while (this.view.frozen()) await new Promise((r) => setTimeout(r, 100)); }

  /** Brings item's text into view. {ok}, or {why, gone}, or {cancelled} (another jump, another pane). */
  async go(item) {
    const token = ++this.token, gone = () => token !== this.token || this.sid() !== item.sid;
    getSelection()?.removeAllRanges();             // a selection would hold the lines back
    this.clear();
    try {
      this.send({ t: "locate", id: item.id });
      const at = await this.wait((m) => m.t === "located" && m.id === item.id);
      if (gone()) return { cancelled: true };
      if (at.n === null || at.n === undefined) return { why: at.why, gone: true };
      const needle = at.size ? part(item.text, at.start, at.size) : "";
      this.view.holding = true;                    // a phone following the output trims no line it loads
      for (let step = 0; ; step++) {
        await this.settled();
        if (gone()) return { cancelled: true };
        if (step > STEPS) return { why: "The lines did not come: try again." };
        const v = this.view, first = v.hist.length ? v.hist[0].n : v.top;
        if (at.n < first && first > v.oldest) {          // older lines first, in one page
          v.loading = true;
          this.send({ t: "more", before: first, to: at.n - CONTEXT });
          await this.wait((m) => m.t === "hist" && m.mode === "prepend");
          continue;
        }
        if (!v.recordAt(at.n)) return { why: "These lines are no longer in this session's history.", gone: true };
        const f = this.foldAt(at.n);
        if (!f) break;
        const folds = this.widgets.folds;
        if (folds.records(f).some((r) => r.gone)) {     // its lines were never sent: asked for
          folds.ask(f, "show");
          const m = await this.wait((x) => x.t === "lines");
          if (m.error) return { why: m.error };
        } else {
          folds.open.add(f.n);
          this.widgets.keepPlace(() => folds.apply());
        }
      }
      if (gone()) return { cancelled: true };
      const shown = this.reveal(at.n, needle);
      if (!shown) return { why: "The line could not be shown." };
      return shown instanceof Range || !needle ? { ok: true } : { ok: true, note: "Its text was not found there: this is the line it was on." };
    } catch (e) {
      return gone() ? { cancelled: true } : { why: e.message };
    } finally {
      this.view.holding = false;
      this.view.loading = false;
    }
  }

  // a fold the line is in that is not open
  foldAt(n) {
    const folds = this.widgets.folds;
    for (const f of folds.items.values()) if (f.n <= n && n <= f.to && !folds.open.has(f.n)) return f;
    return null;
  }

  /** Where line n's text is now: a Range over it (in the rows, or in a rendered widget), else
   *  the element that shows the line. Looked up anew each time: a widget may have been drawn. */
  target(n, needle) {
    const rec = this.view.recordAt(n);
    if (!rec) return null;
    for (const [anchor, h] of this.widgets.hosts) {
      if (h.raw || !h.rows.includes(rec.el)) continue;
      const body = anchor.shadowRoot?.querySelector(".wg .body");
      return (needle && body && rangeOf(textNodes(body), needle)) || anchor;
    }
    const nodes = [];
    let letters = 0;
    for (let k = n; k < n + SPAN && letters < needle.length + 200; k++) {
      const r = this.view.recordAt(k);
      if (!r) break;
      if (!r.el.classList.contains("wg-hid")) nodes.push(...textNodes(r.el));
      letters += loose(r.txt).length;
    }
    return (needle && rangeOf(nodes, needle)) || rec.el;
  }

  reveal(n, needle) {
    const w = this.widgets, term = this.term;
    w.pin = null;
    const first = this.target(n, needle);
    if (!first) return false;
    w.following = false;                           // set now: the scroll event comes later
    const t = term.getBoundingClientRect();
    term.scrollTop += first.getBoundingClientRect().top - t.top - term.clientHeight / 3;
    this.find = () => this.target(n, needle);
    w.pin = () => { const at = this.find(); this.mark(at); return at; };
    w.keepPlace(() => w.update(false, true));     // the regions near it drawn, its place kept
    this.mark(this.target(n, needle));
    this.view.updateBottom();
    return first;
  }

  /** The bookmark's text (a Range, or the element shown when it was not found) marked by a
   *  wavy line on its left, from its first row to its last; the text keeps its own look. The
   *  line lies in the terminal's scrolled content, so it scrolls with the text. */
  mark(at) {
    const line = (this.line ??= Object.assign(document.createElement("div"), { id: "markline" }));
    if (!line.isConnected) this.term.append(line);
    this.at = line.range = at ?? null;               // for the page's own checks
    const live = at instanceof Range ? at.startContainer.isConnected : at?.isConnected;
    const rects = !live ? [] : at instanceof Range ? [...at.getClientRects()].filter((r) => r.height) : [at.getBoundingClientRect()];
    if (!rects.length) { line.hidden = true; return; }
    const t = this.term.getBoundingClientRect(), top = Math.min(...rects.map((r) => r.top)), bottom = Math.max(...rects.map((r) => r.bottom));
    line.style.cssText = `top:${Math.round(top - t.top + this.term.scrollTop)}px;height:${Math.round(bottom - top)}px;left:${this.term.scrollLeft + 6}px`;   // in the terminal's 8 px gutter, beside the text
    line.hidden = false;
  }

  /** No bookmark shown: its mark goes, the view is the user's again. */
  clear() {
    this.widgets.pin = null;
    this.find = null;
    this.at = null;
    if (this.line) { this.line.hidden = true; this.line.range = null; }
  }
}
