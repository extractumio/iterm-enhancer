// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Blobs in the terminal's output (AC-56): a run of rows of encoded data that a tool printed
// (base64 of an image in a CSS file, a hex dump; outparse.js blobRuns) is one line in sight,
// "⋯ 6.2 KB of base64 · 41 lines" with Show and Copy. Its first row holds the line in its shadow
// root (widgetui.js); the other rows are hidden (wg-hid). Their text stays in the page: a
// selection across them copies it all. Only runs near the view are looked at, as image buttons.
import { blobRuns } from "./outparse.js";
import { hostOf } from "./widgetui.js";

const AROUND = 400;          // rows looked at beyond those near the view: a blob may start or go on there

const size = (n) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

export class Blobs {
  /** view: the TermView; click: the shadow roots' click handler; skip(el): a row a widget has. */
  constructor({ view, click, skip }) {
    Object.assign(this, { view, click, skip });
    this.open = new Set();       // keys of blobs the user opened
    this.reset();
  }

  reset() { this.hosts = new Map(); }      // first row -> {rows, key, run}

  /** A row of a collapsed blob was drawn again (the screen): it is put right at once. */
  torn() { return [...this.hosts.values()].some((b) => b.rows.some((el) => !el.isConnected)); }

  /** near: the rows near the view, top to bottom (linkmarks.js nearRows). */
  pass(near) {
    // a blob that lost a row (the screen drew it again, the history dropped it) lets the rest show
    for (const [anchor, b] of this.hosts) {
      if (b.rows.every((el) => el.isConnected)) continue;
      this.drop(anchor, b);
      this.hosts.delete(anchor);
    }
    if (!near.length) return;
    const v = this.view, all = v.hist.concat(v.screen.filter(Boolean));
    const first = all.findIndex((r) => r.el === near[0]);
    if (first < 0) return;
    let last = first;
    for (let k = first; k < all.length; k++) if (all[k].el === near.at(-1)) { last = k; break; }
    const a = Math.max(0, first - AROUND), part = all.slice(a, Math.min(all.length, last + AROUND + 1));
    for (const run of blobRuns(part.map((r) => ({ text: r.txt })))) {
      const rows = part.slice(run.from, run.to + 1).map((r) => r.el);
      if (rows.some((el) => this.skip(el))) continue;
      const was = this.hosts.get(rows[0]);
      if (was && was.rows.length === rows.length) continue;           // as it is
      if (was) this.drop(rows[0], was);
      const key = `${rows[0].textContent.trim().slice(0, 64)}:${run.chars}`;
      this.hosts.set(rows[0], { rows, key, run });
      this.draw(rows[0]);
    }
  }

  /** The blob's line, and its rows hidden or shown. */
  draw(anchor) {
    const b = this.hosts.get(anchor), shown = this.open.has(b.key);
    const label = `${size(b.run.chars)} of ${b.run.kind} · ${b.rows.length} lines`;
    const root = hostOf(anchor, this.click);
    let line = root.querySelector(".blob");
    if (!line) {
      line = document.createElement("span");
      line.className = "blob";
      line.dataset.wg = "";
      for (const [act, title] of [["blob", ""], ["blobcopy", "Copy the data"]]) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.dataset.act = act;
        if (title) { btn.title = title; btn.textContent = "Copy"; }
        line.append(btn);
      }
      root.replaceChildren(line, document.createElement("slot"));
    }
    const toggle = line.querySelector('[data-act="blob"]');
    toggle.textContent = shown ? `Hide ${label}` : `⋯ ${label}`;
    toggle.title = shown ? "Hide the data" : "Show the data";
    anchor.classList.toggle("wg-blob", !shown);
    for (const el of b.rows.slice(1)) el.classList.toggle("wg-hid", !shown);
  }

  drop(anchor, b) {
    for (const el of b.rows) el.classList.remove("wg-hid");
    if (!anchor.isConnected) return;
    anchor.classList.remove("wg-blob");
    anchor.shadowRoot?.querySelector(".blob")?.remove();
  }

  /** Show or Hide pressed on the blob of row `anchor`; Copy: its data, joined. */
  toggle(anchor) {
    const b = this.hosts.get(anchor);
    if (!b) return;
    if (!this.open.delete(b.key)) this.open.add(b.key);
    this.draw(anchor);
  }
  text(anchor) { return this.hosts.get(anchor)?.rows.map((el) => el.textContent.trim()).join("") ?? ""; }
  has(el) { return this.hosts.has(el); }
}
