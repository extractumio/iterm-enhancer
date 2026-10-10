// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The output widgets of the terminal view (AC-56): code, Markdown and Mermaid regions of the
// output (outparse.js) drawn as widgets, and a button after each image file named in it. A
// widget is a shadow root on its region's first row (widgetui.js); in its rendered form the
// region's other rows are hidden (wg-hid), in Raw they show as before. Nothing is added to the
// history's or the screen's children: term.js counts them as rows and blocks.
import { Blobs } from "./blobs.js";
import { nearRows } from "./linkmarks.js";
import { imagePaths, scan } from "./outparse.js";
import { figure, hostOf, imageButtons, loadImage, loadStyles, regionWidget, zoomDiagram, zoomImage } from "./widgetui.js";

const STEADY = 150;          // ms a region on the screen stays as it is before it is rendered
const CACHED = 150;          // rendered widgets kept, the least recently shown go first
const IMAGES_OPEN = 50;      // images kept open
const THROTTLE = 100;        // ms between scans of streaming output
const NEAR = 2;              // screens above and below the view whose history regions are drawn
const RECHECK = 10000;       // ms before an image file that was not there is looked for again
const BOXED = /^\s*[│┃║]|[│┃║]\s*$/;   // a row of a drawn box or table: an image would open inside it

// FNV-1a: a region is known by its kind, language and text
function hash(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193);
  return (h >>> 0).toString(36) + s.length.toString(36);
}

// A row as outparse.js reads it: its text (a wrapped row keeps its spaces) and whether the
// program colored it.
function info(r) {
  if (!r) return { text: "", eol: true, styled: false };
  return (r.wgInfo ??= {
    text: r.data.e ? r.txt : r.el.textContent.replace(/[︎️]/g, ""),
    eol: !!r.data.e,
    styled: r.data.r.some(([, fg, bg]) => (fg != null && fg !== "R") || (bg != null && bg !== "R")),
  });
}

// a dark background ("#rrggbb" or "rgb(r, g, b)") gets Mermaid's dark theme
function dark(bg = "") {
  const hex = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(bg), rgb = /(\d+)\D+(\d+)\D+(\d+)/.exec(bg);
  const [r, g, b] = hex ? hex.slice(1).map((v) => parseInt(v, 16)) : rgb ? rgb.slice(1).map(Number) : [0, 0, 0];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 128;
}

export class Widgets {
  /** view: the TermView; boxes: #hist and #screen; resolve(path): {url, path} or {error} for
   *  an image path as printed; exists(path): whether that file is there; copy(text);
   *  onZoomClosed(): the viewer closed. */
  constructor({ view, term, boxes, resolve, exists, copy, onZoomClosed }) {
    Object.assign(this, { view, term, boxes, resolve, exists, copy, onZoomClosed });
    this.modes = new Map();      // region key -> "raw" or "on", as the user chose
    this.cache = new Map();      // region key + theme -> its widget element
    this.sources = new WeakMap(); // widget -> its region's source text (for Copy)
    this.seen = new Map();       // key of a screen region -> when it was first seen as it is
    this.images = new Map();     // image key -> {url, path, error} of an open image
    this.lastScan = 0;
    this.styled = false;         // widgets wait for their style sheet
    this.reset();
    this.click = (b, root) => this.onClick(b, root);
    this.blobs = new Blobs({ view, click: this.click, skip: (el) => this.inRegion.has(el) || this.hosts.has(el) });
    loadStyles().then(() => { this.styled = true; this.keepPlace(() => this.update(true)); });
    // scrolled, or shown again (Files, View), or another size: regions come near the view
    let timer = 0;
    const near = () => { clearTimeout(timer); timer = setTimeout(() => { this.markImages(); if (this.far) this.keepPlace(() => this.update(false, true)); }, 150); };
    // following the output: as the user left it (a size change or a widget growing is not a scroll away)
    this.following = true;
    term.addEventListener("scroll", () => { this.following = view.atBottom(); near(); }, { passive: true });
    // the terminal's size changes with the window, the keyboard, Files docked or not; shown
    // again (it was 0 high behind Files or View), the regions in sight are drawn at once
    let shownH = 0;
    new ResizeObserver(() => {
      const h = term.clientHeight, back = !shownH && h > 0;
      shownH = h;
      this.keepPlace(() => { this.sizes(); if (back) this.update(false, true); });
      near();
    }).observe(term);
  }

  /** Another session: nothing of the last one stays attached. */
  reset() {
    this.final = [];             // history regions that no longer change: {rows, key, region}
    this.settledN = null;        // the history line the next scan starts at (null: its first)
    this.hosts = new Map();      // a region's first row -> {key, raw, rows, live}: what is attached
    this.imageHosts = new Set();
    this.found = new Map();      // printed image path -> {ok, at}: whether the pane's file is there
    this.inRegion = new WeakSet(); // rows a widget has (no blob line there: the widget shows them)
    this.blobs?.reset();
    clearTimeout(this.steady); clearTimeout(this.later);
  }

  setTheme(theme) {
    this.dark = dark(theme.bg);
    const pal = theme.ansi ?? [];
    const s = this.term.style;
    // the token colors of the panel's highlighter, from the profile's ANSI colors
    [["kw", 13], ["str", 10], ["num", 11], ["fn", 12], ["type", 14], ["tag", 9]].forEach(([k, i]) => { if (pal[i]) s.setProperty(`--tk-${k}`, pal[i]); });
  }

  /** The largest an image in the history is: half the terminal's width and height; it is
   *  centered across the terminal's width. */
  sizes() {
    const t = this.term, s = t.style, cs = getComputedStyle(t);
    const inner = t.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    const tw = `${Math.round(inner)}px`, w = `${Math.round(t.clientWidth / 2)}px`, h = `${Math.round(t.clientHeight / 2)}px`;
    if (!t.clientHeight || (s.getPropertyValue("--wg-tw") === tw && s.getPropertyValue("--wg-ih") === h)) return;
    s.setProperty("--wg-tw", tw);                  // the terminal's text width: images are centered across it
    s.setProperty("--wg-iw", w);
    s.setProperty("--wg-ih", h);
    s.setProperty("--wg-dh", h);
  }

  /** After the history or the screen changed (term.js, inside its keepBottom). full: the rows
   *  were made again or older ones came first: scan all of the history again. Streaming output
   *  is scanned at most every THROTTLE ms, unless a widget lost one of its rows (the screen drew
   *  them again), which is put right at once, or `now` (the user's own change). */
  update(full = false, now = false) {
    const v = this.view;
    if (!v.theme || !this.styled) return;
    clearTimeout(this.later);
    // the screen drew a row with image buttons again: they come back now, not a moment later
    if ([...this.imageHosts].some((el) => !el.isConnected) || this.blobs.torn()) this.markImages(true);
    const wait = this.lastScan + THROTTLE - performance.now();
    const torn = [...this.hosts.values()].some((h) => h.live && h.rows.some((el) => !el.isConnected));
    if (!full && !now && wait > 0 && !torn) { this.later = setTimeout(() => this.keepPlace(() => this.update()), wait); return; }
    this.lastScan = performance.now();
    if (full) { this.final = []; this.settledN = null; }
    const hist = v.hist, screen = v.screen;
    const base = hist.length ? hist[0].n : 0;
    const i0 = this.settledN === null ? 0 : Math.min(hist.length, Math.max(0, this.settledN - base));
    const rows = hist.slice(i0).concat(screen);
    const histPart = hist.length - i0;
    // the output ends at the cursor's row; in a coding agent's pane at its input box's top rule
    // (what is typed there is not output, and changes with every key)
    let end = screen.findIndex((r) => r?.el.querySelector(".cur"));
    const boxTop = v.agent && v.box ? screen.filter(Boolean)[v.box.top]?.el : null;
    const boxAt = boxTop ? screen.findIndex((r) => r?.el === boxTop) : -1;
    if (boxAt >= 0 && (end < 0 || boxAt < end)) end = boxAt;
    const { found, settled } = scan(rows.map(info), histPart + (end >= 0 ? end : screen.length));
    // regions wholly in the history before `settled` are final; the screen changes
    let cut = Math.min(settled, histPart);
    for (const r of [...found].reverse()) if (r.to >= cut && r.from < cut) cut = r.from;
    const at = performance.now(), live = [];
    for (const r of found) {
      const key = `${r.kind}:${r.lang}:${hash(r.text)}`, part = rows.slice(r.from, r.to + 1);
      if (r.to < cut) this.final.push({ rows: part, key, region: r });
      else {
        live.push({ rows: part, key, region: r, live: true });
        if (!this.seen.has(key)) this.seen.set(key, at);
      }
    }
    this.settledN = i0 + cut < hist.length ? hist[i0 + cut].n : hist.length ? hist.at(-1).n + 1 : null;
    for (const k of this.seen.keys()) if (!live.some((l) => l.key === k)) this.seen.delete(k);
    // a region whose first line was dropped (trim) is no longer one: its other rows show again
    this.final = this.final.filter((f) => f.rows[0].n >= base);
    this.apply([...this.final, ...live.filter((l) => at - this.seen.get(l.key) >= STEADY || this.cache.has(this.cacheKey(l.key)))]);
    clearTimeout(this.steady);
    if (live.some((l) => !this.cache.has(this.cacheKey(l.key)))) this.steady = setTimeout(() => this.keepPlace(() => this.update(false, true)), STEADY);
    this.markImages();
  }

  cacheKey(key) { return `${key}:${this.dark ? "d" : "l"}`; }
  // rendered unless the user chose Raw; a diagram guessed from text without a fence starts in Raw
  raw(key, region) { return (this.modes.get(key) ?? (region.guessed ? "raw" : "on")) === "raw"; }

  // The widgets attached to their rows; rows no longer in a region show again. A history
  // region attached as it should be is passed over (its rows stay as they are); a screen row
  // may have been drawn again, so a screen region's rows are all looked at. A history region
  // far from the view (NEAR screens) stays text until it comes near: a long history is not
  // drawn all at once.
  apply(list) {
    const hosts = new Map();
    const t = this.term.getBoundingClientRect(), lo = t.top - NEAR * t.height, hi = t.bottom + NEAR * t.height;
    // following the output, the view goes to the end after this: measured from there
    const ahead = this.view.sticking ? this.term.scrollHeight - this.term.clientHeight - this.term.scrollTop : 0;
    const near = (els) => !this.term.hidden && t.height > 0 && els[0].getBoundingClientRect().top - ahead < hi
      && els.at(-1).getBoundingClientRect().bottom - ahead > lo;
    this.far = false;
    const show = (els, keep = new Set()) => { for (const el of els) if (!keep.has(el)) el.classList.remove("wg-hid"); };
    const detach = (anchor, was) => {
      show(was.rows);
      for (const el of was.rows) this.inRegion.delete(el);
      anchor.classList.remove("wg-host", "wg-raw");
      anchor.shadowRoot?.replaceChildren(document.createElement("slot"));
    };
    list = list.filter(({ rows }) => rows.length && !rows.some((r) => !r));
    // positions first, then changes: a read after a change would lay out the whole history again
    const away = new Set(list.filter((l) => !l.live && !this.hosts.has(l.rows[0].el) && !near(l.rows.map((r) => r.el))));
    for (const item of list) {
      const { rows, key, region, live } = item;
      const anchor = rows[0].el, raw = this.raw(key, region), els = rows.map((r) => r.el);
      const was = this.hosts.get(anchor);
      if (away.has(item)) { this.far = true; continue; }
      hosts.set(anchor, { key, raw, rows: els, live });
      for (const el of els) this.inRegion.add(el);
      const root = hostOf(anchor, this.click);
      let mine = root.firstElementChild;
      if (!live && was?.key === key && was.raw === raw && was.rows.length === els.length && was.rows.at(-1) === els.at(-1)
        && mine?.dataset.key === key) continue;
      if (was && was.key !== key) detach(anchor, was);
      else if (was) show(was.rows, new Set(els));                 // rows the region no longer has
      if (mine?.dataset.key !== key) {
        mine = this.widget(key, region);
        const used = mine.getRootNode().host;
        if (used && used !== anchor && hosts.has(used)) mine = this.build(key, region);   // printed twice: one each
        root.replaceChildren(mine, document.createElement("slot"));
      }
      if (!raw) mine.draw();
      anchor.classList.add("wg-host");
      anchor.classList.toggle("wg-raw", raw);
      for (const el of els.slice(1)) el.classList.toggle("wg-hid", !raw);
    }
    for (const [anchor, was] of this.hosts) if (!hosts.has(anchor)) detach(anchor, was);
    this.hosts = hosts;
  }

  build(key, region) {
    const wg = regionWidget(region, { key, dark: this.dark, change: (fn) => this.keepPlace(fn, wg.getRootNode().host ?? null, false),
      raw: () => { this.modes.set(key, "raw"); this.keepPlace(() => this.update(false, true)); } });
    this.sources.set(wg, region.text);
    return wg;
  }

  widget(key, region) {
    const ck = this.cacheKey(key);
    let wg = this.cache.get(ck);
    if (wg) { this.cache.delete(ck); this.cache.set(ck, wg); return wg; }
    this.cache.set(ck, wg = this.build(key, region));
    for (const k of [...this.cache.keys()].slice(0, Math.max(0, this.cache.size - CACHED))) this.cache.delete(k);
    return wg;
  }

  /** Runs fn, a change to the page, keeping the reader's place: `row` where it is on screen
   *  (the row the user acted on: an image opened under it, Raw, Markdown; `user` false: a widget
   *  that changed by itself, kept unless the user follows the output); else the end while
   *  following the output, else the first row wholly in sight. Not while text is selected or a
   *  finger is down: the page must not change under them. */
  keepPlace(fn, row = null, user = true) {
    if (this.view.frozen()) { setTimeout(() => this.keepPlace(fn, row, user), 300); return; }
    const t = this.term.getBoundingClientRect(), r = row?.isConnected ? row.getBoundingClientRect() : null;
    let at = r && r.bottom > t.top && r.top < t.bottom ? row : null;             // only a row in sight
    if ((!at || !user) && (this.following || this.view.atBottom())) {
      fn();
      this.view.toBottom();                                      // following the output: its end stays in sight
      this.following = true;
      return;
    }
    // a row cut by the top edge may grow inside: the first one wholly in sight
    at ??= nearRows(this.term, this.boxes).find((el) => el.getBoundingClientRect().top >= t.top);
    const before = at?.getBoundingClientRect().top;
    fn();
    if (at?.isConnected && before !== undefined) this.term.scrollTop += at.getBoundingClientRect().top - before;
    this.view.updateBottom();
  }

  onClick(b, root) {
    const act = b.dataset.act, wg = b.closest(".wg"), key = wg?.dataset.key;
    if (act === "raw" || act === "on") {
      this.modes.set(key, act);
      if (act === "on" && wg.dataset.failed !== undefined) {
        // it could not be drawn: made again, so it tries again (fbd may be back)
        for (const [k, w] of this.cache) if (w === wg) this.cache.delete(k);
        root.replaceChildren(document.createElement("slot"));
      }
      return this.keepPlace(() => this.update(false, true), root.host);
    }
    if (act === "copy") return this.copy(this.sources.get(wg) ?? "");
    if (act === "blob") return this.keepPlace(() => this.blobs.toggle(root.host), root.host);
    if (act === "blobcopy") return this.copy(this.blobs.text(root.host));
    if (act === "zoom") return zoomDiagram(b, this.onZoomClosed);
    if (act === "zoomimg") return zoomImage(b, this.onZoomClosed);
    if (act === "img") return this.openImage(root.host, b.dataset.path);
    if (act === "unimg") {
      const fig = b.closest("figure");
      this.images.delete(this.imageKey(root.host, fig.dataset.path));
      this.keepPlace(() => fig.remove(), root.host);
    }
  }

  /** The file a printed image path names is there: a button only for one that opens. Asked once
   *  (in the background, the button comes when the answer does); "not there" is asked again
   *  after RECHECK ms, as a program may save it a moment later. */
  there(value) {
    const k = this.found.get(value), now = performance.now();
    if (k && (k.ok || now - k.at < RECHECK)) return k.ok;
    this.found.set(value, { ok: false, at: now });          // asked: not again meanwhile
    this.exists(value).then((ok) => { this.found.set(value, { ok, at: performance.now() }); if (ok) this.markImages(); }, () => {});
    return false;
  }

  imageKey(row, path) { return `${hash(row.textContent)}:${path}`; }

  async openImage(row, path) {
    const k = this.imageKey(row, path);
    if (this.images.has(k)) return;
    this.images.set(k, { pending: true });
    let got;
    try { got = await loadImage(await this.resolve(path)); } catch (e) { got = { error: e.message || String(e) }; }
    this.images.set(k, got);
    for (const key of [...this.images.keys()].slice(0, Math.max(0, this.images.size - IMAGES_OPEN))) this.images.delete(key);
    this.keepPlace(() => this.markImages(true), row);           // the line clicked stays where it is
    // ... unless the image would open out of sight: then just enough to show it
    const fig = row.isConnected && [...(row.shadowRoot?.querySelectorAll("figure") ?? [])].find((f) => f.dataset.path === path);
    if (fig) {
      const t = this.term.getBoundingClientRect(), b = fig.getBoundingClientRect().bottom;
      if (b > t.bottom) this.term.scrollTop += Math.min(b - t.bottom + 8, row.getBoundingClientRect().top - t.top);
      this.following = this.view.atBottom();
      this.view.updateBottom();
    }
  }

  /** The image buttons of the rows near the view (a long listing of image files would
   *  otherwise give thousands of rows a shadow root), and the images open there. */
  markImages(now = false) {
    if (!this.styled) return;
    if (!now) { clearTimeout(this.imgTimer); this.imgTimer = setTimeout(() => this.keepPlace(() => this.markImages(true)), 60); return; }
    // blobs first: the rows they hide get no image buttons
    this.blobs.pass(nearRows(this.term, this.boxes));
    const rows = nearRows(this.term, this.boxes), hosts = new Set();
    let line = [];
    const flush = () => {
      const last = line.at(-1);
      const text = line.map((el) => el.textContent.replace(/[︎️]/g, "")).join("");
      line = [];
      // not a widget's own row, a drawn table's or an agent's input box and footer
      if (!last || this.hosts.has(last) || this.blobs.has(last) || last.closest(".inbox, .foot") || BOXED.test(text)) return;
      const paths = imagePaths(text).filter((p) => this.there(p.value));
      if (!paths.length) return;
      hosts.add(last);
      const root = hostOf(last, this.click);
      const want = paths.map((p) => p.value).join("\n");
      let [span, figs] = [root.querySelector(".wgi"), root.querySelector(".figs")];
      if (span?.dataset.paths !== want) {
        [span, figs] = imageButtons(paths);
        span.dataset.paths = want;
        root.replaceChildren(document.createElement("slot"), span, figs);
      }
      for (const p of paths) {
        const got = this.images.get(this.imageKey(last, p.value));
        const shown = [...figs.children].find((f) => f.dataset.path === p.value);
        if (got && !got.pending && !shown) figs.append(figure(p.value, got));
        if (!got && shown) shown.remove();
      }
    };
    for (const el of rows) {
      line.push(el);
      if (el.classList.contains("eol")) flush();
    }
    flush();
    for (const el of this.imageHosts) if (!hosts.has(el) && !this.hosts.has(el) && !this.blobs.has(el)) el.shadowRoot?.replaceChildren(document.createElement("slot"));
    this.imageHosts = hosts;
  }
}
