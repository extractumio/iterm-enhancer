// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Virtual file tree. Folders are paged from the backend (500 rows per page), so a
// folder with 500,000 entries costs one scrollbar and ~40 DOM rows. Expanded folders
// are spliced into their parent by index: the row count of a folder is its total plus
// the row counts of its expanded children (plus one row while a new item is being named).
// Row counts are memoized per structural version, so rendering a frame is cheap.

import { api, basename, dirname, esc, fmtSize, isDirKind, isUnder, join, rebase, type Page, type Row } from "./api";
import { fileIcon, folderIcon, chevron } from "./icons";
import { InlineEdit, type EditMode } from "./tree-edit";

export type { EditMode } from "./tree-edit";

const PAGE = 500;
const OVERSCAN = 12;

interface DirNode {
  path: string;
  loaded: boolean;
  writable: boolean;
  error?: string;
  total: number;
  gen: number;
  pages: Map<number, Row[]>;
  pending: Set<number>;
  /** expanded children with their index in this folder's (filtered) listing */
  children: Map<string, { idx: number; node: DirNode }>;
  sorted?: { idx: number; node: DirNode }[];
  memo?: { v: number; rows: number };
}

type RowDesc =
  | { kind: "entry"; dir: DirNode; idx: number; depth: number; row?: Row }
  | { kind: "note"; dir: DirNode; depth: number; text: string; spin?: boolean }
  | { kind: "edit"; dir: DirNode; depth: number };

export interface TreeCallbacks {
  open(path: string): void;                              // file activated
  openWindow(path: string): void;                        // ⌘-click: file in the viewer window
  changed(): void;                                       // expanded/selected changed → persist
  context(path: string | null, isDir: boolean, ev: MouseEvent): void;
  commit(mode: EditMode, dir: string, name: string, path?: string): Promise<string | null>;
}

export class Tree {
  root: DirNode | null = null;
  private nodes = new Map<string, DirNode>();
  expanded = new Set<string>();
  selected = new Set<string>();
  showHidden = true;
  private cursor = -1;               // row index of the keyboard cursor
  private anchor = -1;               // row index where a ⇧-range starts
  private filter = "";
  private rowH = 22;
  private version = 0;               // bumps on every structural change; invalidates memos
  private renderQueued = false;
  private epoch = 0;                 // bumps on re-root; stale async results are dropped
  private spacer = document.createElement("div");
  private layer = document.createElement("div");
  private edit: InlineEdit;

  constructor(private el: HTMLElement, private cb: TreeCallbacks) {
    this.spacer.className = "spacer";
    this.layer.className = "rows";
    this.edit = new InlineEdit({ commit: cb.commit, rerender: () => this.touch(), done: () => el.focus() });
    el.append(this.spacer, this.layer, this.edit.el);
    el.addEventListener("scroll", () => this.queue());
    new ResizeObserver(() => this.themeChanged()).observe(el);
    el.addEventListener("click", (e) => this.onClick(e));
    el.addEventListener("contextmenu", (e) => this.onContext(e));
    el.addEventListener("keydown", (e) => this.onKey(e));
  }

  // ── public API ──────────────────────────────────────────────────────────────

  /** Show `root`, restoring `expanded`, `selected` and the scroll position. */
  async setRoot(root: string, expanded: string[], selected: string[], scroll = 0) {
    this.epoch++;
    this.edit.cancel();
    this.nodes.clear();
    this.expanded = new Set();
    this.selected = new Set(selected);
    this.cursor = this.anchor = -1;
    this.el.scrollTop = 0;
    this.root = this.node(root);
    const epoch = this.epoch;
    await this.load(this.root, 0);
    // restore expanded folders parent-first so each can be located in its parent
    for (const p of [...expanded].sort((a, b) => a.length - b.length)) {
      if (epoch !== this.epoch) return;
      if (p.startsWith(root + "/")) await this.expand(p, false);
    }
    if (epoch !== this.epoch) return;
    this.render();
    if (scroll > 0) this.el.scrollTop = scroll;
    else if (selected[0]) await this.reveal(selected[0]);
    this.queue();
  }

  setFilter(filter: string) { this.filter = filter.trim(); this.reload(); }

  setShowHidden(show: boolean) {
    this.showHidden = show;
    if (!show) for (const p of this.selected) if (this.isHiddenPath(p)) this.selected.delete(p);
    this.reload();
  }

  /** Re-read everything (keeps expanded, selection and scroll). */
  reload() {
    if (this.root) void this.setRoot(this.root.path, [...this.expanded], [...this.selected], this.el.scrollTop);
  }

  /** Folders changed on disk or through a panel: re-read the ones we show. The next
   *  response replaces their pages (gen -1) and re-locates expanded children. */
  async refreshDirs(dirs: string[]) {
    await Promise.all(dirs.map((d) => this.nodes.get(d)).filter((n) => n?.loaded).map((n) => {
      n!.gen = -1;
      return this.load(n!, 0, true);
    }));
  }

  /** After a rename, carry expanded folders, selection and cached nodes to the new path. */
  renamed(from: string, to: string) {
    this.expanded = new Set([...this.expanded].map((p) => rebase(p, from, to)));
    this.selected = new Set([...this.selected].map((p) => rebase(p, from, to)));
    const parent = this.nodes.get(dirname(from));
    const c = parent?.children.get(basename(from));
    if (parent && c) {
      parent.children.delete(basename(from));
      for (const k of [...this.nodes.keys()]) if (isUnder(k, from)) this.nodes.delete(k); // reloads lazily
      const node = this.node(to);
      parent.children.set(basename(to), { idx: c.idx, node });
      void this.load(node, 0);
    }
    this.touch();
  }

  collapseAll() {
    this.expanded.clear();
    this.root?.children.clear();
    this.cb.changed();
    this.touch();
  }

  focus() { this.el.focus(); }
  get scroll() { return this.el.scrollTop; }
  get cursorPath(): string | null { return this.descAt(this.cursor)?.p ?? [...this.selected][0] ?? null; }
  /** May the panel write in folder `dir` (as last reported by the backend). */
  isWritable(dir: string) { return this.nodes.get(dir)?.writable ?? false; }

  /** Row height follows the theme's font size; read it only when layout may have changed. */
  themeChanged() {
    this.rowH = parseFloat(getComputedStyle(this.el).getPropertyValue("--row")) || 22;
    this.queue();
  }

  /** Target folder for "new file": the selected folder, or the folder of the selected file. */
  targetDir(): string | null {
    if (!this.root) return null;
    const p = this.cursorPath;
    if (!p) return this.root.path;
    const d = this.descAt(this.cursor);
    return (d ? d.isDir : this.nodes.has(p)) ? p : dirname(p);
  }

  async startCreate(dir: string, mode: "file" | "folder") {
    if (!this.root) return;
    if (dir !== this.root.path && !this.expanded.has(dir)) await this.expand(dir);
    this.edit.start({ mode, dir });
  }

  startRename(path: string) { this.edit.start({ mode: "rename", dir: dirname(path), path }); }

  // ── data ────────────────────────────────────────────────────────────────────

  private touch() { this.version++; this.queue(); }

  private node(path: string): DirNode {
    let n = this.nodes.get(path);
    if (!n) {
      n = { path, loaded: false, writable: false, total: 0, gen: 0, pages: new Map(), pending: new Set(), children: new Map() };
      this.nodes.set(path, n);
    }
    return n;
  }

  private query(extra: Record<string, string | number | boolean | undefined>) {
    return { hidden: this.showHidden, filter: this.filter || undefined, limit: PAGE, ...extra };
  }

  private async load(n: DirNode, page: number, force = false): Promise<void> {
    if (n.pending.has(page) && !force) return;
    n.pending.add(page);
    const epoch = this.epoch;
    try {
      let r: Page;
      for (;;) {
        r = await api<Page>("GET", "/api/ls", { query: this.query({ path: n.path, offset: page * PAGE }) });
        if (r.status !== "loading") break;           // backend still reading a huge folder
        await new Promise((res) => setTimeout(res, 300));
        if (epoch !== this.epoch) return;
      }
      if (epoch !== this.epoch) return;
      const reread = n.loaded && r.gen !== n.gen;
      if (r.gen !== n.gen) n.pages.clear();          // folder was re-read: old pages are stale
      Object.assign(n, { loaded: true, error: r.status === "error" ? r.error : undefined, total: r.total, gen: r.gen, writable: r.writable });
      if (r.status !== "error") n.pages.set(page, r.entries);
      if (reread && n.children.size) void this.relocate(n); // entries may have shifted
    } catch (e: any) {
      n.error = e.message; n.loaded = true;
    } finally {
      n.pending.delete(page);
      this.touch();
    }
  }

  /** Re-find expanded children after their folder was re-read; drop the ones that are gone. */
  private async relocate(n: DirNode) {
    const epoch = this.epoch;
    for (const [name, c] of [...n.children]) {
      const idx = await this.locate(n, name).catch(() => null);
      if (epoch !== this.epoch) return;
      if (idx == null || idx >= n.total) this.collapse(join(n.path, name), false);
      else c.idx = idx;
    }
    n.sorted = undefined;
    this.touch();
  }

  /** Index of `name` inside folder `n`, asking the backend if the page is not loaded. */
  private async locate(n: DirNode, name: string): Promise<number | null> {
    for (const [p, rows] of n.pages) {
      const i = rows.findIndex((r) => r.n === name);
      if (i >= 0) return p * PAGE + i;
    }
    const r = await api<Page>("GET", "/api/ls", { query: this.query({ path: n.path, offset: 0, limit: 1, locate: name }) });
    return r.located ?? null;
  }

  private async expand(path: string, persist = true) {
    if (!this.root || !path.startsWith(this.root.path)) return;
    const parent = this.nodes.get(dirname(path));
    if (!parent || (!this.expanded.has(parent.path) && parent !== this.root)) return;
    const epoch = this.epoch;
    const idx = await this.locate(parent, basename(path));
    if (idx == null || epoch !== this.epoch) return;  // gone, filtered out, or re-rooted meanwhile
    const node = this.node(path);
    parent.children.set(basename(path), { idx, node });
    parent.sorted = undefined;
    this.expanded.add(path);
    this.touch();
    await this.load(node, 0);
    if (persist) this.cb.changed();
  }

  private collapse(path: string, persist = true) {
    for (const p of [...this.expanded]) {
      if (!isUnder(p, path)) continue;
      this.expanded.delete(p);
      const parent = this.nodes.get(dirname(p));
      if (parent) { parent.children.delete(basename(p)); parent.sorted = undefined; }
    }
    if (this.edit.edit && isUnder(this.edit.edit.dir, path)) this.edit.cancel();
    if (persist) this.cb.changed();
    this.touch();
  }

  toggle(path: string) {
    if (this.expanded.has(path)) this.collapse(path); else void this.expand(path);
  }

  // ── row arithmetic ──────────────────────────────────────────────────────────

  private rowsOf(n: DirNode): number {
    if (n.memo?.v === this.version) return n.memo.rows;
    const extra = this.edit.creatingIn(n.path);
    let rows = 1 + extra;
    if (n.loaded && !n.error && n.total > 0) {
      rows = n.total + extra;
      for (const c of n.children.values()) rows += this.rowsOf(c.node);
    }
    n.memo = { v: this.version, rows };
    return rows;
  }

  private rowAt(n: DirNode, i: number, depth: number): RowDesc {
    if (this.edit.creatingIn(n.path)) {
      if (i === 0) return { kind: "edit", dir: n, depth };
      i--;
    }
    if (!n.loaded) return { kind: "note", dir: n, depth, text: "reading…", spin: true };
    if (n.error) return { kind: "note", dir: n, depth, text: `⚠ ${n.error}` };
    if (n.total === 0) return { kind: "note", dir: n, depth, text: this.filter ? "no matches" : "empty" };
    let pos = i, consumed = 0;
    n.sorted ??= [...n.children.values()].sort((a, b) => a.idx - b.idx);
    for (const c of n.sorted) {
      const block = c.idx - consumed + 1;
      if (pos < block) return this.entry(n, consumed + pos, depth);
      pos -= block;
      consumed = c.idx + 1;
      const sub = this.rowsOf(c.node);
      if (pos < sub) return this.rowAt(c.node, pos, depth + 1);
      pos -= sub;
    }
    return this.entry(n, consumed + pos, depth);
  }

  private entry(n: DirNode, idx: number, depth: number): RowDesc {
    // a stale child index can point past the end until relocate() runs: never fetch there
    if (idx >= n.total) return { kind: "note", dir: n, depth, text: "" };
    const page = Math.floor(idx / PAGE);
    const row = n.pages.get(page)?.[idx - page * PAGE];
    if (!row && !n.pending.has(page)) void this.load(n, page);
    return { kind: "entry", dir: n, idx, depth, row };
  }

  /** Global row index of entry `idx` in folder `n`, or -1 if an ancestor is collapsed. */
  private indexOf(n: DirNode, idx: number): number {
    let base = 0;
    if (n !== this.root) {
      const parent = this.nodes.get(dirname(n.path));
      const me = parent?.children.get(basename(n.path));
      const at = parent && me ? this.indexOf(parent, me.idx) : -1;
      if (at < 0) return -1;
      base = at + 1;
    }
    let offset = idx + this.edit.creatingIn(n.path);
    for (const c of n.children.values()) if (c.idx < idx) offset += this.rowsOf(c.node);
    return base + offset;
  }

  async reveal(path: string) {
    const parent = this.nodes.get(dirname(path));
    const idx = parent ? await this.locate(parent, basename(path)) : null;
    const at = parent && idx != null ? this.indexOf(parent, idx) : -1;
    if (at < 0) return;
    this.cursor = this.anchor = at;
    this.scrollIntoView(at);
    this.queue();
  }

  private scrollIntoView(i: number) {
    const top = i * this.rowH, h = this.el.clientHeight;
    if (top < this.el.scrollTop) this.el.scrollTop = top;
    else if (top + this.rowH > this.el.scrollTop + h) this.el.scrollTop = top + this.rowH - h;
  }

  // ── rendering ───────────────────────────────────────────────────────────────

  queue() {
    if (this.renderQueued) return;
    this.renderQueued = true;
    requestAnimationFrame(() => { this.renderQueued = false; this.render(); });
  }

  private render() {
    if (!this.root) { this.layer.innerHTML = ""; return; }
    const total = this.rowsOf(this.root);
    this.spacer.style.height = `${total * this.rowH}px`;
    const first = Math.max(0, Math.floor(this.el.scrollTop / this.rowH) - OVERSCAN);
    const last = Math.min(total, Math.ceil((this.el.scrollTop + this.el.clientHeight) / this.rowH) + OVERSCAN);
    const html: string[] = [];
    let editRow = -1, editDepth = 0;
    for (let i = first; i < last; i++) {
      const d = this.rowAt(this.root, i, 0);
      if (d.kind === "edit" || (d.kind === "entry" && d.row && this.edit.renaming(join(d.dir.path, d.row.n)))) {
        editRow = i; editDepth = d.depth;
      }
      html.push(this.rowHtml(d, i));
    }
    this.layer.style.transform = `translateY(${first * this.rowH}px)`;
    this.layer.innerHTML = html.join("");
    this.edit.place(editRow, editDepth, this.rowH);
  }

  private rowHtml(d: RowDesc, i: number): string {
    const guides = `<span class="guides">${"<i></i>".repeat(d.depth)}</span>`;
    if (d.kind === "edit") {
      const icon = this.edit.edit?.mode === "folder" ? folderIcon(false) : fileIcon(this.edit.value);
      return `<div class="row editing" data-i="${i}">${guides}<span class="chev"></span>${icon}</div>`;
    }
    if (d.kind === "note")
      return `<div class="row note" data-i="${i}">${guides}<span class="chev"></span>${d.spin ? '<span class="spin"></span>' : ""}<span class="muted">${esc(d.text)}</span></div>`;
    if (!d.row)
      return `<div class="row" data-i="${i}">${guides}<span class="chev"></span><span class="skeleton"></span></div>`;
    const r = d.row, p = join(d.dir.path, r.n), isDir = isDirKind(r.k);
    const open = isDir && this.expanded.has(p);
    const renaming = this.edit.renaming(p);
    const cls = ["row", this.selected.has(p) ? "sel" : "", i === this.cursor ? "cursor" : "", renaming ? "editing" : ""].join(" ");
    return `<div class="${cls}" data-i="${i}" data-p="${esc(p)}" data-d="${isDir ? 1 : 0}" title="${esc(r.n)}">${guides}` +
      `<span class="chev${open ? " open" : ""}">${isDir ? chevron : ""}</span>` +
      (isDir ? folderIcon(open) : fileIcon(r.n)) +
      (renaming ? "" : `<span class="name${isDir ? " dir" : ""}${r.n.startsWith(".") ? " hidden" : ""}">${this.highlight(r.n)}</span>` +
        (r.k === "l" || r.k === "L" ? '<span class="link" title="symlink">↪</span>' : "") +
        (isDir ? "" : `<span class="size">${fmtSize(r.s)}</span>`)) + "</div>";
  }

  private highlight(name: string) {
    const i = this.filter ? name.toLowerCase().indexOf(this.filter.toLowerCase()) : -1;
    if (i < 0) return esc(name);
    const j = i + this.filter.length;
    return esc(name.slice(0, i)) + "<mark>" + esc(name.slice(i, j)) + "</mark>" + esc(name.slice(j));
  }

  private isHiddenPath(p: string) {
    return this.root ? p.slice(this.root.path.length).split("/").some((s) => s.startsWith(".")) : false;
  }

  // ── interaction ─────────────────────────────────────────────────────────────

  private rowEl(e: Event) { return (e.target as HTMLElement).closest<HTMLElement>(".row[data-p]"); }

  private select(p: string, i: number, mode: "one" | "toggle" | "range" = "one") {
    if (mode === "range" && this.anchor >= 0) {
      this.selected.clear();
      const [a, b] = this.anchor < i ? [this.anchor, i] : [i, this.anchor];
      for (let k = a; k <= b; k++) { const x = this.descAt(k); if (x) this.selected.add(x.p); }
    } else if (mode === "toggle") {
      if (!this.selected.delete(p)) this.selected.add(p);
      this.anchor = i;
    } else {
      this.selected = new Set([p]);
      this.anchor = i;
    }
    this.cursor = i;
    this.cb.changed();
    this.queue();
  }

  private onClick(e: MouseEvent) {
    if ((e.target as HTMLElement).closest(".inline-edit")) return;
    const row = this.rowEl(e);
    if (!row) return;
    const p = row.dataset.p!, i = Number(row.dataset.i), isDir = row.dataset.d === "1";
    if (e.shiftKey) return this.select(p, i, "range");
    if (e.metaKey && !isDir) { this.select(p, i); return this.cb.openWindow(p); }
    if (e.metaKey || e.altKey) return this.select(p, i, "toggle");
    this.select(p, i);
    if (isDir) this.toggle(p); else this.cb.open(p);
  }

  private onContext(e: MouseEvent) {
    e.preventDefault();
    const row = this.rowEl(e);
    if (!row) return this.cb.context(null, true, e);
    const p = row.dataset.p!;
    if (!this.selected.has(p)) this.select(p, Number(row.dataset.i));
    this.cb.context(p, row.dataset.d === "1", e);
  }

  private descAt(i: number) {
    if (!this.root || i < 0) return null;
    const d = this.rowAt(this.root, i, 0);
    return d.kind === "entry" && d.row ? { d, p: join(d.dir.path, d.row.n), isDir: isDirKind(d.row.k) } : null;
  }

  private onKey(e: KeyboardEvent) {
    if (!this.root || e.metaKey || e.ctrlKey || e.altKey) return;
    const total = this.rowsOf(this.root);
    const move = (to: number) => {
      to = Math.max(0, Math.min(total - 1, to));
      const x = this.descAt(to);
      if (x) this.select(x.p, to, e.shiftKey ? "range" : "one");
      this.cursor = to;
      this.scrollIntoView(to);
      this.queue();
    };
    const page = Math.max(1, Math.floor(this.el.clientHeight / this.rowH) - 1);
    const cur = this.descAt(this.cursor);
    switch (e.key) {
      case "ArrowDown": move(this.cursor + 1); break;
      case "ArrowUp": move(this.cursor < 0 ? 0 : this.cursor - 1); break;
      case "PageDown": move(this.cursor + page); break;
      case "PageUp": move(this.cursor - page); break;
      case "Home": move(0); break;
      case "End": move(total - 1); break;
      case "ArrowRight":
        if (cur?.isDir && !this.expanded.has(cur.p)) void this.expand(cur.p);
        else if (cur?.isDir) move(this.cursor + 1);
        break;
      case "ArrowLeft":
        if (cur?.isDir && this.expanded.has(cur.p)) this.collapse(cur.p);
        else if (cur && cur.d.dir !== this.root) {
          const parent = cur.d.dir.path;
          void this.reveal(parent).then(() => { this.selected = new Set([parent]); this.cb.changed(); this.queue(); });
        }
        break;
      case "Enter": if (cur) cur.isDir ? this.toggle(cur.p) : this.cb.open(cur.p); break;
      case "F2": if (cur) this.startRename(cur.p); break;
      default: return;
    }
    e.preventDefault();
  }
}
