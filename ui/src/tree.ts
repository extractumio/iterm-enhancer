// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Virtual file tree. Folders are paged from the backend (500 rows per page), so a
// folder with 500,000 entries costs one scrollbar and ~40 DOM rows. Expanded folders
// are spliced into their parent by index (row arithmetic in tree-rows.ts, row HTML in
// tree-render.ts, "expand all" in tree-expand.ts).

import { api, basename, dirname, isDirKind, isUnder, join, rebase, type Page } from "./api";
import { rowHtml, type RowView } from "./tree-render";
import { PAGE, indexOf, rowAt, rowsOf, type DirNode, type RowDesc, type RowsHost } from "./tree-rows";
import { expandAll, summary } from "./tree-expand";
import { InlineEdit, type EditMode } from "./tree-edit";

export type { EditMode } from "./tree-edit";

const OVERSCAN = 12;

export interface TreeCallbacks {
  open(path: string): void;                              // file activated
  openWindow(path: string): void;                        // ⌘-click: file in the viewer window
  changed(): void;                                       // expanded/selected changed → persist
  context(path: string | null, isDir: boolean, ev: MouseEvent): void;
  commit(mode: EditMode, dir: string, name: string, path?: string): Promise<string | null>;
  notify(message: string): void;                         // e.g. what "expand all" did
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
  private expandRun = 0;             // bumps on expand all and any collapse; cancels a running one
  private spacer = document.createElement("div");
  private layer = document.createElement("div");
  private edit: InlineEdit;
  /** The tree's state as the row arithmetic reads it. */
  private rows: RowsHost = ((t: Tree) => ({
    get version() { return t.version; },
    get filter() { return t.filter; },
    get root() { return t.root; },
    nodes: t.nodes,
    creatingIn: (p: string) => t.edit.creatingIn(p),
    load: (n: DirNode, page: number) => void t.load(n, page),
  }))(this);

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
    // restore expanded folders level by level: each is located in its (already open) parent
    const levels = new Map<number, string[]>();
    for (const p of expanded) {
      if (!isUnder(p, root) || p === root) continue;                    // also for root "/"
      const depth = p.split("/").length;
      levels.set(depth, [...(levels.get(depth) ?? []), p]);
    }
    for (const depth of [...levels.keys()].sort((a, b) => a - b)) {
      if (epoch !== this.epoch) return;
      await Promise.all(levels.get(depth)!.map((p) => this.expand(p, false)));
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

  /** Expand every folder below `start` (the root by default), bounded (AC-31). Returns
   *  what was done, or null when cancelled by a re-root, a collapse or another expand. */
  async expandAll(start = this.root?.path): Promise<string | null> {
    if (!this.root || !start) return null;
    const run = ++this.expandRun, epoch = this.epoch, root = this.root.path;
    const alive = () => run === this.expandRun && epoch === this.epoch;
    const wasOpen = new Set(this.expanded);           // the user's open folders stay open
    const r = await expandAll(start, root, {
      alive,
      expand: async (p) => {
        if (p !== root && !this.expanded.has(p)) await this.expand(p, false);
        const n = this.nodes.get(p);
        return alive() && n?.loaded && !n.error && (p === root || this.expanded.has(p)) ? { total: n.total, rows: n.pages.get(0) ?? [] } : null;
      },
      collapse: (p) => { if (!wasOpen.has(p)) this.collapse(p, false, false); },
    });
    if (r.cancelled) return null;
    this.cb.changed();
    return summary(r);
  }

  private expandAllFrom(p: string) { void this.expandAll(p).then((m) => { if (m) this.cb.notify(m); }); }

  collapseAll() {
    this.expandRun++;
    this.expanded.clear();
    // every cached folder forgets its open children, or re-opening one shows them again
    for (const n of this.nodes.values()) { n.children.clear(); n.sorted = undefined; }
    if (this.edit.edit) this.edit.cancel();
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
      if (idx == null || idx >= n.total) this.collapse(join(n.path, name), false, false); // not the user: expand all goes on
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
    // a failed lookup (an unreachable host, AC-37) counts as not found: the folder's error row says why
    const r = await api<Page>("GET", "/api/ls", { query: this.query({ path: n.path, offset: 0, limit: 1, locate: name }) }).catch(() => null);
    return r?.located ?? null;
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

  private collapse(path: string, persist = true, cancelExpand = true) {
    if (cancelExpand) this.expandRun++;
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

  async reveal(path: string) {
    const parent = this.nodes.get(dirname(path));
    const idx = parent ? await this.locate(parent, basename(path)) : null;
    const at = parent && idx != null ? indexOf(parent, idx, this.rows) : -1;
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
    const total = rowsOf(this.root, this.rows);
    this.spacer.style.height = `${total * this.rowH}px`;
    const first = Math.max(0, Math.floor(this.el.scrollTop / this.rowH) - OVERSCAN);
    const last = Math.min(total, Math.ceil((this.el.scrollTop + this.el.clientHeight) / this.rowH) + OVERSCAN);
    const html: string[] = [];
    let editRow = -1, editDepth = 0;
    for (let i = first; i < last; i++) {
      const d = rowAt(this.root, i, 0, this.rows);
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
    const view: RowView = d.kind === "entry" ? { kind: "entry", dir: d.dir.path, depth: d.depth, row: d.row }
      : d.kind === "note" ? d
      : { kind: "edit", depth: d.depth, folder: this.edit.edit?.mode === "folder", name: this.edit.value };
    return rowHtml(view, i, { expanded: this.expanded, selected: this.selected, cursor: this.cursor, filter: this.filter, renaming: (p) => this.edit.renaming(p) });
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
    if (e.altKey && isDir && (e.target as HTMLElement).closest(".chev")) { // macOS: ⌥-click the disclosure arrow
      this.select(p, i);
      return this.expanded.has(p) ? this.collapse(p) : this.expandAllFrom(p);
    }
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
    const d = rowAt(this.root, i, 0, this.rows);
    return d.kind === "entry" && d.row ? { d, p: join(d.dir.path, d.row.n), isDir: isDirKind(d.row.k) } : null;
  }

  private onKey(e: KeyboardEvent) {
    if (!this.root || e.metaKey || e.ctrlKey) return;
    if (e.altKey) {                                       // macOS: ⌥→ / ⌥← on a folder, recursively
      const cur = this.descAt(this.cursor);
      if (!cur?.isDir || (e.key !== "ArrowRight" && e.key !== "ArrowLeft")) return;
      e.preventDefault();
      return e.key === "ArrowRight" ? this.expandAllFrom(cur.p) : this.collapse(cur.p);
    }
    const total = rowsOf(this.root, this.rows);
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
