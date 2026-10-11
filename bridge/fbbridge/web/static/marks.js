// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Bookmarks of the terminal (AC-57): a selection kept with a note, on the Mac for every
// signed-in browser (the bridge's bookmarks.py). Here: the dialog that makes or edits one, the
// marks on the terminal's right edge, the bar that shows the one brought into view, and the list
// of all of them below the sessions. Text from the terminal is shown as text only.
import { icon } from "./icons.js";
import { Jumper } from "./markjump.js";
import { markPicker } from "./markpick.js";
import { railSlots } from "./marktext.js";

const $ = (id) => document.getElementById(id);
const ANSWER = 10000;          // ms the dialog waits for the Mac to keep a bookmark
const ARMED = 3000;            // ms a Delete pressed once waits for the second press

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const firstLine = (text, max = 90) => { const l = text.trim().split("\n")[0]; return l.length > max ? `${l.slice(0, max)}…` : l; };
const when = (s) => new Date(s * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

// Delete asks again: a second press within 3 s deletes.
function armed(button, label, act) {
  let timer = 0;
  button.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!timer) {
      button.textContent = "Delete?"; button.classList.add("armed");
      timer = setTimeout(() => { timer = 0; button.classList.remove("armed"); button.replaceChildren(...label()); }, ARMED);
      return;
    }
    clearTimeout(timer); timer = 0;
    act();
  });
}

export class Marks {
  /** view, widgets, term: the terminal's; send(msg) to the bridge; sid(): the pane shown;
   *  alive(sid): whether a pane is open; pick(sid): show it (and close the drawer); toast(text);
   *  onChange(): the session list is drawn again (its orange strips); copy(text): to the clipboard. */
  constructor({ view, widgets, term, send, sid, alive, pick, toast, onChange, copy }) {
    Object.assign(this, { view, widgets, term, send, sid, alive, pick, toast, onChange });
    this.items = new Map();
    this.replies = new Map();    // ref -> resolve, for the dialog
    this.shown = null;           // the bookmark in the bar
    this.jumper = new Jumper({ view, widgets, term, send, sid });
    markPicker({ term, main: $("main"), button: $("markadd"), copyButton: $("markcopy"), view,
      onPick: (sel) => this.edit(null, sel), onCopy: copy });
    $("markbox").querySelector("form").addEventListener("submit", (e) => this.submit(e));
    $("markbox").addEventListener("close", () => { this.editing = null; });
    $("markbox").addEventListener("cancel", (e) => { if (this.saving) e.preventDefault(); });   // Esc while saving
    $("mbclose").onclick = () => this.hideBar();
    $("mbedit").onclick = () => this.shown && this.edit(this.shown);
    $("mbprev").onclick = () => this.step(-1);
    $("mbnext").onclick = () => this.step(1);
    armed($("mbdel"), () => ["Delete"], () => { if (this.shown) this.send({ t: "unmark", id: this.shown.id }); });
    $("marksbtn").onclick = () => this.toggleList();
    $("marksfab").onclick = () => this.togglePane();
    $("marksfab").addEventListener("mousedown", (e) => e.preventDefault());   // the terminal keeps the keys
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") this.togglePane(false); });
    // a press anywhere else closes it
    document.addEventListener("pointerdown", (e) => { if (!$("markpane").hidden && !e.target.closest("#markpane, #marksfab")) this.togglePane(false); });
    new ResizeObserver(() => this.drawRail()).observe(term);
  }

  /** Every message from the bridge (after app.js dropped those of another pane). */
  seen(m) {
    this.jumper.seen(m);
    switch (m.t) {
      case "marks":
        this.items = new Map(m.items.map((i) => [i.id, i]));
        if (m.error) this.toast(`Bookmarks: ${m.error}`, 8000);
        return this.changed();
      case "mark":
        this.items.set(m.item.id, m.item);
        if (this.shown?.id === m.item.id) this.showBar(m.item, this.why);
        return this.changed();
      case "unmark":
        this.items.delete(m.id);
        if (this.shown?.id === m.id) this.hideBar();
        return this.changed();
      case "marked": return this.replies.get(m.ref)?.(m);
      case "hist":                                 // another pane: the bar and the list were of the last one
        if (m.mode === "reset" && this.shown && this.shown.sid !== m.sid) this.hideBar();
        else if (m.mode === "reset") this.jumper.clear();     // its lines are numbered anew: no mark on them
        if (m.mode === "reset" && m.sid !== this.paneSid) this.togglePane(false);
      // falls through
      case "screen": requestAnimationFrame(() => this.drawRail()); return;   // the pane's range changed
      case "layout": requestAnimationFrame(() => this.drawList());                  // a session closed or came back
    }
  }

  sids() { return new Set([...this.items.values()].map((i) => i.sid)); }
  busy() { return $("markbox").open; }
  changed() { this.drawRail(); this.drawList(); this.drawPane(); this.onChange(); }
  mine() { return [...this.items.values()].filter((i) => i.sid === this.sid()).sort((a, b) => a.n - b.n); }

  // ---------- the dialog ----------

  /** A new bookmark of selection sel ({n, text}), or item's note edited. */
  edit(item, sel = null) {
    // one ref per dialog: a save sent again after no answer is kept once (bookmarks.py)
    this.editing = { ref: Math.random().toString(36).slice(2), ...(item ? { id: item.id } : { sid: this.sid(), ...sel, text: sel.text.slice(0, 2000) }) };
    $("marktext").textContent = item ? item.text : sel.text;
    $("marknote").value = item?.note ?? "";
    $("markerr").hidden = true;
    $("markbox").querySelector(".primary").disabled = false;
    $("markbox").showModal();
    $("marknote").focus();
  }

  async submit(e) {
    if (e.submitter?.value !== "save" || !this.editing) return;   // Cancel closes it
    e.preventDefault();
    const save = $("markbox").querySelector(".primary"), ed = this.editing, ref = ed.ref, note = $("marknote").value;
    save.disabled = true; this.saving = true;
    const reply = await new Promise((resolve) => {
      const timer = setTimeout(() => resolve({ error: "The Mac did not answer: is the connection back?" }), ANSWER);
      this.replies.set(ref, (m) => { clearTimeout(timer); resolve(m); });
      this.send(ed.id ? { t: "mark", ref, id: ed.id, note } : { t: "mark", ref, sid: ed.sid, n: ed.n, text: ed.text, note });
    });
    this.replies.delete(ref);
    save.disabled = false; this.saving = false;
    if (reply.error) { $("markerr").textContent = reply.error; $("markerr").hidden = false; return; }
    $("markbox").close();
    if (!ed.id) this.toast("Bookmarked.");
  }

  // ---------- the bar ----------

  showBar(item, why = null) {
    this.shown = item; this.why = why;
    const mine = this.mine(), i = mine.findIndex((x) => x.id === item.id);
    $("mbnote").textContent = item.note || firstLine(item.text);
    $("mbnote").classList.toggle("quiet", !item.note);
    $("mbwhy").textContent = why ?? ""; $("mbwhy").hidden = !why;
    $("mbtext").textContent = item.text; $("mbtext").hidden = !why;
    $("mbpos").textContent = i >= 0 && mine.length > 1 ? `${i + 1}/${mine.length}` : "";
    $("mbprev").hidden = $("mbnext").hidden = !(i >= 0 && mine.length > 1);
    $("markbar").style.top = `${this.term.offsetTop + 8}px`;
    $("markbar").hidden = false;
  }
  hideBar() { this.shown = null; $("markbar").hidden = true; this.jumper.clear(); }
  step(d) {
    const mine = this.mine(), i = mine.findIndex((x) => x.id === this.shown?.id);
    if (i >= 0 && mine.length) this.jump(mine[(i + d + mine.length) % mine.length]);
  }

  /** Shows item's pane and brings its text into view; the bar says how it went. */
  async jump(item) {
    if (!this.alive(item.sid)) return this.showBar(item, "Its session is no longer open: here is what was selected.");
    const other = item.sid !== this.sid();
    const opened = other ? this.jumper.wait((m) => m.t === "hist" && m.mode === "reset" && m.sid === item.sid) : null;
    this.pick(item.sid);
    if (opened) { try { await opened; } catch (e) { return this.showBar(item, e.message); } }
    this.showBar(item);
    const r = await this.jumper.go(item);
    if (r.cancelled || this.shown?.id !== item.id) return;
    if (r.why) this.showBar(item, r.gone ? `${r.why} Here is what was selected.` : `Could not bring it into view: ${r.why}`);
    else if (r.note) this.showBar(item, r.note);
  }

  // ---------- the marks on the terminal's edge ----------

  drawRail() {
    this.placeFab();
    const rail = $("markrail"), term = this.term, v = this.view, mine = this.mine();
    if (!mine.length || term.hidden || !term.clientHeight || !v.theme) { rail.hidden = true; this.railKey = ""; return; }
    // left of a scroll bar (clientWidth ends there); "left": in the wide layout the terminal's
    // column, not #main, places it (style.css)
    const h = term.clientHeight - 16;
    const css = `top:${term.offsetTop + 8}px;height:${h}px;left:${term.offsetLeft + term.clientLeft + term.clientWidth - 18}px`;
    const slots = railSlots(mine, v.oldest, v.top + v.screen.length, h - 14);
    const key = css + JSON.stringify(slots);       // the screen changes often; the marks seldom
    if (key === this.railKey) return;
    this.railKey = key;
    rail.style.cssText = css;
    const marks = slots.map(({ top, items }) => {
      const b = el("button", "mk");
      b.type = "button";
      b.style.top = `${top}px`;
      const what = items.map((i) => i.note || firstLine(i.text, 60));
      b.title = items.length > 1 ? `${items.length} bookmarks:\n${what.join("\n")}` : what[0];
      b.setAttribute("aria-label", `Bookmark: ${b.title}`);
      if (items.length > 1) b.dataset.count = String(items.length);
      b.onclick = () => this.jump(items[0]);
      return b;
    });
    rail.replaceChildren(...marks);
    rail.hidden = false;
  }

  // ---------- all bookmarks, below the sessions ----------

  toggleList(open = $("marklist").hidden) {
    $("marklist").hidden = !open;
    $("marksbtn").setAttribute("aria-expanded", String(open));
    if (open) this.drawList();
  }

  drawList() {
    const count = this.items.size;
    $("markcount").textContent = count ? String(count) : "";
    if ($("marklist").hidden) return;
    const all = [...this.items.values()].sort((a, b) => b.at - a.at);
    if (!all.length) return $("marklist").replaceChildren(el("p", "empty", "No bookmarks yet. Select text in a terminal and press + beside it."));
    $("marklist").replaceChildren(this.listOf(all, true));
  }

  /** A list of bookmarks: note, the start of the text, and (all: its session's title, host, when
   *  and whether it closed); a press brings one into view, × (twice) deletes it. */
  listOf(items, all) {
    const ul = el("ul");
    for (const item of items) {
      const li = el("li"), b = el("button", "mkitem");
      b.type = "button";
      b.append(el("span", "note" + (item.note ? "" : " quiet"), item.note || firstLine(item.text)));
      if (item.note) b.append(el("span", "snip", firstLine(item.text)));
      const meta = el("span", "meta", (all ? [item.title, item.host, when(item.at)] : [when(item.at)]).filter(Boolean).join(" · "));
      if (all && !this.alive(item.sid)) meta.append(el("span", "closed", "closed"));
      b.append(meta);
      b.onclick = () => { this.togglePane(false); this.jump(item); };
      const del = el("button", "mkdel");
      del.type = "button";
      del.title = "Delete this bookmark";
      const label = () => [icon("x", 14)];
      del.append(...label());
      armed(del, label, () => this.send({ t: "unmark", id: item.id }));
      li.append(b, del);
      ul.append(li);
    }
    return ul;
  }

  // ---------- this session's bookmarks, from the button at the terminal's top right ----------

  /** The button stands at the terminal's top right, left of the marks, with the number of the
   *  session's bookmarks on it; only while it has one at least. */
  placeFab() {
    const fab = $("marksfab"), term = this.term, n = this.sid() ? this.mine().length : 0;
    const on = n > 0 && !term.hidden && term.clientHeight > 0 && !!this.view.theme;
    fab.hidden = !on;
    if (!on) return this.togglePane(false);
    $("marksfabn").textContent = String(n);
    fab.setAttribute("aria-label", `Bookmarks of this session: ${n}`);
    fab.style.top = `${term.offsetTop + 8}px`;
    fab.style.left = `${term.offsetLeft + term.clientLeft + term.clientWidth - 18 - 8 - 32}px`;
    if (!$("markpane").hidden) this.placePane();
  }
  placePane() {
    const fab = $("marksfab"), pane = $("markpane");
    pane.style.top = `${fab.offsetTop + fab.offsetHeight + 8}px`;
    pane.style.left = `${Math.max(8, fab.offsetLeft + fab.offsetWidth - pane.offsetWidth)}px`;
  }
  /** Opens or closes the list of this session's bookmarks (a second press closes it). */
  togglePane(open = $("markpane").hidden) {
    $("markpane").hidden = !open;
    this.paneSid = open ? this.sid() : null;
    $("marksfab").setAttribute("aria-expanded", String(open));
    if (open) { this.drawPane(); this.placePane(); }
  }
  drawPane() {
    if ($("markpane").hidden) return;
    const mine = this.mine();
    $("markpane").replaceChildren(el("h2", "", "Bookmarks of this session"),
      this.listOf(mine, false));
  }
}
