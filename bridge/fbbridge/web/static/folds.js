// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Folds of the history (AC-56): parts the bridge found (fold.py: an edit's diff, a long command
// or output of a coding agent's tools, rows of encoded data, nearly equal lines) are one line,
// "⋯ 26 lines of diff", with Show and Copy. Lines the bridge has not sent (opening a pane,
// older lines) are empty rows until the user shows them: then it is asked for them. The fold's
// first row holds the line in its shadow root (widgetui.js); its other rows are hidden.
import { hostOf } from "./widgetui.js";

const MARKS = /[︎️]/g;          // the text-style marks render.js adds

export class Folds {
  /** view: the TermView; click: the shadow roots' click handler; send(msg) to the bridge;
   *  copy(text); notify(text): a short message. */
  constructor({ view, click, send, copy, notify }) {
    Object.assign(this, { view, click, send, copy, notify });
    this.reset();
  }

  /** Another pane, or its scrollback cleared (its line numbers mean other lines now). */
  reset() {
    for (const [anchor, b] of this.hosts ?? []) this.drop(anchor, b);
    this.items = new Map();      // first line -> {n, to, label}, as the bridge told
    this.open = new Set();       // first lines of folds the user opened
    this.hosts = new Map();      // first row -> {f, rows, open}: as drawn
    this.rows = new WeakSet();   // rows of a fold (no widget, no image button there)
    this.waiting = new Map();    // first line -> "show" | "again" | {resolve, reject}: its lines are asked for
  }

  add(items) { for (const f of items ?? []) this.items.set(f.n, f); }
  has(el) { return this.hosts.has(el) || this.rows.has(el); }

  /** Every fold drawn on the rows the history has; quick when nothing changed. A fold whose
   *  oldest rows are not loaded is drawn on those that are; one whose rows all went is forgotten. */
  apply() {
    const hist = this.view.hist, base = hist.length ? hist[0].n : 0, drawn = new Map();
    for (const f of this.items.values()) {
      if (hist.length && f.to < base) { this.items.delete(f.n); this.open.delete(f.n); continue; }
      const a = Math.max(0, f.n - base), b = Math.min(hist.length - 1, f.to - base);
      if (a > b) continue;
      const open = this.open.has(f.n), was = this.hosts.get(hist[a].el);
      if (was?.f === f && was.open === open && was.rows.length === b - a + 1 && was.rows.at(-1) === hist[b].el) {
        drawn.set(hist[a].el, was);               // as drawn: its rows are not looked at again
        continue;
      }
      const rows = hist.slice(a, b + 1).map((r) => r.el);
      drawn.set(rows[0], { f, rows, open });
      this.draw(rows[0], f, rows, open);
    }
    for (const [anchor, b] of this.hosts) if (!drawn.has(anchor)) this.drop(anchor, b);
    this.hosts = drawn;
  }

  draw(anchor, f, rows, open) {
    for (const el of rows) this.rows.add(el);
    const root = hostOf(anchor, this.click);
    let line = root.querySelector(".fold");
    if (!line) {
      line = document.createElement("span");
      line.className = "fold";
      line.dataset.wg = "";
      for (const act of ["fold", "foldcopy"]) {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.act = act;
        if (act === "foldcopy") { b.textContent = "Copy"; b.title = "Copy these lines"; }
        line.append(b);
      }
      root.replaceChildren(line, document.createElement("slot"));
    }
    const toggle = line.querySelector('[data-act="fold"]');
    toggle.textContent = open ? `Hide ${f.label.replace(/^⋯\s*/, "")}` : f.label;
    toggle.title = open ? "Hide these lines" : "Show these lines";
    anchor.classList.toggle("wg-fold", !open);
    for (const el of rows.slice(1)) el.classList.toggle("wg-hid", !open);
  }

  drop(anchor, b) {
    for (const el of b.rows) { el.classList.remove("wg-hid"); this.rows.delete(el); }
    if (!anchor.isConnected) return;
    anchor.classList.remove("wg-fold");
    anchor.shadowRoot?.querySelector(".fold")?.remove();
  }

  // the history's records of a fold (its top may have been dropped)
  records(f) {
    const hist = this.view.hist, base = hist.length ? hist[0].n : 0;
    return hist.slice(Math.max(0, f.n - base), Math.max(0, f.to - base + 1));
  }

  textOf(f) {
    return this.records(f).map((r) => r.el.textContent.replace(MARKS, "").replace(/\s+$/, "")).join("\n");
  }

  /** Show or Hide pressed on the fold of row `anchor`; lines not sent yet are asked for first. */
  toggle(anchor) {
    const f = this.hosts.get(anchor)?.f;
    if (!f) return;
    if (this.open.delete(f.n)) return this.apply();
    if (this.records(f).some((r) => r.gone)) return this.ask(f, "show");
    this.open.add(f.n);
    this.apply();
  }

  /** Copy pressed: the fold's lines, as they are. Lines not here yet are asked for: with the
   *  clipboard's own promise where the browser has one (the copy stays in the click), else the
   *  user presses Copy again once they are here. */
  copyOf(anchor) {
    const f = this.hosts.get(anchor)?.f;
    if (!f) return;
    if (!this.records(f).some((r) => r.gone)) return this.copy(this.textOf(f));
    if (window.isSecureContext && navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
      const text = new Promise((resolve, reject) => this.ask(f, { resolve, reject }));
      navigator.clipboard.write([new ClipboardItem({ "text/plain": text.then((t) => new Blob([t], { type: "text/plain" })) })])
        .then(() => this.notify("Copied."), () => this.notify("The lines are here now: press Copy again."));
      return;
    }
    this.ask(f, "again");
  }

  // its lines asked for, from the first the history still has
  ask(f, then) {
    this.waiting.set(f.n, then);
    this.send({ t: "unfold", n: Math.max(f.n, this.records(f)[0]?.n ?? f.n), to: f.to });
  }

  /** The bridge could not send lines first.. (the page shows why): folds waiting for them stop. */
  failed(first) {
    for (const [n, then] of this.waiting) {
      const f = this.items.get(n);
      if (!f || first < f.n || first > f.to) continue;
      this.waiting.delete(n);
      then?.reject?.(new Error("not sent"));
    }
  }

  /** The bridge sent lines first.. (they are in the history now): a fold waiting for them opens,
   *  or is copied. */
  filled(first, count) {
    const copies = [];
    for (const [n, then] of this.waiting) {
      const f = this.items.get(n);
      if (!f || f.n > first + count - 1 || f.to < first) continue;
      this.waiting.delete(n);
      if (then === "show") this.open.add(n); else copies.push([f, then]);
    }
    this.apply();                                 // the rows are new: drawn again first
    for (const [f, then] of copies) {
      if (then === "again") this.notify("The lines are here: press Copy again to copy them.");
      else then.resolve(this.textOf(f));
    }
  }
}
