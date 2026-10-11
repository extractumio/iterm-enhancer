// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Search of the shown session's history (AC-58): the magnifier at the terminal's top right opens a
// field with Find; the bridge looks through every line the page could load (search.py), the page
// says how many, goes to the first and steps to the next and the one before, each brought into
// view as a bookmark is (markjump.js: older lines loaded, folds opened, widgets' text) and marked;
// every match is a white mark on the terminal's edge. A second press on the magnifier closes it.
import { Jumper } from "./markjump.js";
import { railBox, railSlots } from "./marktext.js";

const $ = (id) => document.getElementById(id);
const ANSWER = 30000;          // ms the field waits for the Mac's answer

export class Find {
  /** view, widgets, term: the terminal's; send(msg) to the bridge; sid(): the pane shown. */
  constructor({ view, widgets, term, send, sid }) {
    Object.assign(this, { view, widgets, term, send, sid });
    this.hits = []; this.total = 0; this.i = -1; this.q = ""; this.ref = "";
    const hl = globalThis.CSS?.highlights;
    this.jumper = new Jumper({ view, widgets, term, send, sid, exact: true,
      marker: (at) => {
        document.querySelector(".find-at")?.classList.remove("find-at");
        if (at instanceof Range) { if (hl && typeof Highlight !== "undefined") hl.set("find", new Highlight(at)); return; }
        hl?.delete("find");
        at?.classList.add("find-at");                                 // its row, when the text is not found on it
      } });
    $("findfab").onclick = () => this.toggle();
    $("findfab").addEventListener("mousedown", (e) => e.preventDefault());   // the terminal keeps the keys until the field opens
    $("findbox").addEventListener("submit", (e) => { e.preventDefault(); this.find(); });
    $("findprev").onclick = () => this.step(-1);
    $("findnext").onclick = () => this.step(1);
    $("findclose").onclick = () => this.toggle(false);
    $("findq").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.shiftKey) { e.preventDefault(); this.step(-1); }
      if (e.key === "Escape") { e.preventDefault(); this.toggle(false); }
    });
    $("findq").addEventListener("input", () => { if ($("findq").value !== this.q) this.say(""); });
  }

  open() { return !$("findbox").hidden; }

  /** Every message from the bridge (after app.js dropped those of another pane). */
  seen(m) {
    this.jumper.seen(m);
    if (m.t === "found" && m.ref === this.ref) this.found(m);
    if (m.t === "hist" && m.mode === "reset" && this.open()) {
      if (m.sid !== this.paneSid) this.toggle(false);                 // another pane
      else if (this.hits.length || this.pending) this.forget("The history changed: press Find again.");   // its lines are numbered anew
    }
    if ((m.t === "hist" || m.t === "screen") && this.hits.length) requestAnimationFrame(() => this.drawRail());
  }

  /** Opens the field (focused) or closes it, the matches' marks with it. */
  toggle(open = !this.open()) {
    if (!open && !this.open()) return;
    if (open) this.onOpen?.();                                        // the bookmarks' list closes
    $("findbox").hidden = !open;
    $("findfab").setAttribute("aria-expanded", String(open));
    if (open) {
      this.paneSid = this.sid();
      this.place();
      $("findq").focus();
      $("findq").select();
      return;
    }
    this.forget("");
  }

  /** No matches (any more): the marks and the highlight go; text says why. */
  forget(text) {
    this.hits = []; this.total = 0; this.i = -1; this.q = ""; this.ref = ""; this.pending = false;
    this.jumper.token++;
    this.say(text);
    this.jumper.clear();
    this.drawRail();
  }

  place() {
    const fab = $("findfab"), box = $("findbox");
    if (box.hidden || fab.hidden) return;
    box.style.top = `${fab.offsetTop + fab.offsetHeight + 8}px`;
    box.style.left = `${Math.max(8, fab.offsetLeft + fab.offsetWidth - box.offsetWidth)}px`;
  }

  say(text) { $("findn").textContent = text; }

  /** Find: the text asked for anew; the same text again steps to the next match. */
  find() {
    const q = $("findq").value;
    if (!q.trim()) return this.say("Type what to find.");
    if (q === this.q && this.hits.length) return this.step(1);
    if (q === this.q && this.pending) return;                         // on its way already
    this.jumper.token++;
    this.jumper.clear();
    this.q = q; this.hits = []; this.i = -1; this.pending = true;
    this.ref = Math.random().toString(36).slice(2);
    const ref = this.ref;
    this.say("Searching…");
    this.send({ t: "find", ref, q });
    setTimeout(() => { if (this.ref === ref && !this.hits.length && $("findn").textContent === "Searching…") this.say("The Mac did not answer."); }, ANSWER);
  }

  found(m) {
    this.pending = false;
    if (m.error) return this.say(m.error);
    this.hits = m.hits; this.total = m.total; this.i = -1;
    this.drawRail();
    if (!this.hits.length) return this.say("Not found");
    this.step(1);
  }

  /** The next (d 1) or the one before (d -1) match, round the ends. */
  async step(d) {
    const n = this.hits.length;
    if (!n) return;
    this.i = (this.i + d + n) % n;
    await this.go(this.i);
  }

  async go(i) {
    this.i = i;
    const more = this.total > this.hits.length ? "+" : "";
    this.say(`${i + 1} / ${this.hits.length}${more}`);
    this.drawRail();
    const [line, skip] = this.hits[i];
    const r = await this.jumper.goTo(line, this.q, skip);
    if ((r.why || r.note) && this.i === i) this.say(`${i + 1} / ${this.hits.length}${more}: ${r.why ?? "the text is not on that line as shown (a widget may render it otherwise); this is its line."}`);
  }

  /** The matches as white marks on the terminal's edge (as a bookmark's orange ones); the one
   *  shown stands out; a press on one goes there. */
  drawRail() {
    const rail = $("findrail"), v = this.view, box = railBox(this.term);
    if (!this.hits.length || !box || !v.theme) { rail.hidden = true; this.railKey = ""; return; }
    const key = `${box.css}|${v.oldest}|${v.top + v.screen.length}|${this.i}|${this.hits.length}|${this.ref}`;
    if (key === this.railKey) return;                                 // the screen changes often; the marks seldom
    this.railKey = key;
    rail.style.cssText = box.css;
    rail.replaceChildren(...railSlots(this.hits.map(([n], i) => ({ n, i })), v.oldest, v.top + v.screen.length, box.h - 14, 4).map(({ top, items }) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "fm" + (items.some((x) => x.i === this.i) ? " on" : "");
      b.style.top = `${top}px`;
      b.title = items.length > 1 ? `${items.length} matches` : `Match ${items[0].i + 1}`;
      b.setAttribute("aria-label", b.title);
      b.onclick = () => this.go(items[0].i);
      return b;
    }));
    rail.hidden = false;
  }
}
