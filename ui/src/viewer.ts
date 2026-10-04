// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// File tabs and the viewer/editor: CodeMirror for text (editable when the backend says
// the file is writable), rendered Markdown with a Source toggle, images, and a card for
// binary files. Unsaved edits live here, per file, so switching panes never drops them.

import { EditorState, Compartment, type Text } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { editorExtensions } from "./viewer-editor";

import { api, apiOrToast, ApiError, basename, esc, fmtSize, isUnder, rawUrl, rebase, toast, type FileView, type Stamp, type Tab } from "./api";
import { ask } from "./dialogs";
import { fileIcon } from "./icons";
import { languageForFile } from "./highlight";
import { isRenderable, renderDoc, type LinkHost } from "./docview";

export interface ViewerCallbacks {
  changed(): void;                 // tabs / active / view mode changed → persist
  layout(hasTabs: boolean): void;  // show or hide the viewer area
  reveal(path: string): void;      // select the file in the tree
  active?(path: string | null): void; // the active tab changed (the viewer window's path bar)
}

interface Doc {
  file?: FileView;
  error?: string;
  loading?: Promise<void>;
  state?: EditorState;
  saved?: Text;                    // document as last loaded or saved
  dirty: boolean;
  diskChanged: boolean;            // changed on disk while dirty
  saving?: Promise<boolean>;       // one save per file at a time
  deleted?: boolean;               // gone on disk (content kept; a rename may bring it back)
  scroll: Record<string, number>;  // per view mode
  host: string | null;             // where the file lives: every read and save goes there (AC-37)
}


export class Viewer {
  tabs: Tab[] = [];
  active: number | null = null;
  // one document store per host ("" is this Mac, AC-37): the same path on two hosts never
  // shares a tab's contents or unsaved edits; the store follows the shown pane's host
  private stores = new Map<string, Map<string, Doc>>();
  private host = "";
  private get docs(): Map<string, Doc> {
    let m = this.stores.get(this.host);
    if (!m) this.stores.set(this.host, (m = new Map()));
    return m;
  }
  private set docs(m: Map<string, Doc>) { this.stores.set(this.host, m); }
  private editor: EditorView | null = null;
  private lang = new Compartment();
  private shown: { path: string; mode: "rendered" | "source" } | null = null; // on screen now
  private anchors = new Map<string, string>();  // path → anchor to scroll to when it renders
  private render = 0;

  private linksFor(d: Doc, render: number): LinkHost {
    return {
      host: d.host,
      current: () => render === this.render,
      open: (path, anchor) => this.open(path, "auto", anchor),
      prefetched: (path, file) => { if (!this.docs.get(path)?.file) this.doc(path).file = file; },
    };
  }

  constructor(private tabsEl: HTMLElement, private tools: HTMLElement, private body: HTMLElement, private cb: ViewerCallbacks) {
    tabsEl.addEventListener("click", (e) => {
      const t = (e.target as HTMLElement).closest<HTMLElement>(".tab");
      if (!t) return;
      const i = Number(t.dataset.i);
      if ((e.target as HTMLElement).closest(".close")) void this.close(i); else this.activate(i);
    });
    tabsEl.addEventListener("auxclick", (e) => {
      const t = (e.target as HTMLElement).closest<HTMLElement>(".tab");
      if (t && e.button === 1) void this.close(Number(t.dataset.i));
    });
    tools.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-act]");
      if (!b || this.active == null) return;
      const tab = this.tabs[this.active];
      const act = b.dataset.act!;
      if (act === "rendered" || act === "source") {
        this.remember();             // save the scroll of the mode we are leaving
        tab.view = act; this.show(); this.cb.changed();
      } else if (act === "reveal") this.cb.reveal(tab.path);
      else if (act === "open-app") void this.openWithApp(tab.path);
      else if (act === "save") void this.save(tab.path);
    });
    body.addEventListener("click", (e) => this.onBodyClick(e));
  }

  // ── tabs ────────────────────────────────────────────────────────────────────

  setTabs(tabs: Tab[], active: number | null) {
    this.remember();
    this.tabs = tabs.map((t) => ({ path: t.path, view: t.view ?? "auto" }));
    // an unsaved file never loses its tab (another panel or a stale workspace may drop it)
    for (const [p, d] of this.docs) if (d.dirty && !this.tabs.some((t) => t.path === p)) this.tabs.push({ path: p, view: "source" });
    this.active = this.tabs.length ? Math.min(active ?? 0, this.tabs.length - 1) : null;
    // keep memory bounded: only open tabs and unsaved files keep their contents
    const keep = new Set(this.tabs.map((t) => t.path));
    for (const [p, d] of this.docs) if (!keep.has(p) && !d.dirty) this.docs.delete(p);
    this.renderTabs();
    this.show();
  }

  open(path: string, view: Tab["view"] = "auto", anchor?: string) {
    if (anchor) this.anchors.set(path, anchor);
    let i = this.tabs.findIndex((t) => t.path === path);
    if (i < 0) {
      i = this.active == null ? this.tabs.length : this.active + 1; // after the active tab, like an IDE
      this.tabs.splice(i, 0, { path, view });
    }
    this.activate(i);
  }

  activate(i: number) {
    this.remember();
    this.active = i;
    this.tabsChanged();
    void this.revalidate(this.tabs[i].path);
  }

  async close(i: number): Promise<boolean> {
    const t = this.tabs[i];
    const d = this.docs.get(t.path);
    if (d?.dirty) {
      const r = await ask(`Save changes to ${basename(t.path)}?`, "Your changes will be lost if you don't save them.",
        [{ id: "save", label: "Save", primary: true }, { id: "discard", label: "Don't Save", danger: true }, { id: "cancel", label: "Cancel" }]);
      if (r === "cancel") return false;
      if (r === "save" && (!(await this.save(t.path, false, d)) || d.dirty)) return false;  // the document asked about, on its host
    }
    const at = this.tabs.indexOf(t);
    if (at < 0) return true;
    this.remember();
    this.tabs.splice(at, 1);
    this.docs.delete(t.path);
    if (this.active != null && (at < this.active || this.active >= this.tabs.length)) this.active--;
    if (!this.tabs.length) this.active = null;
    this.tabsChanged();
    return true;
  }

  closeActive() { if (this.active != null) void this.close(this.active); }

  cycle(delta: number) {
    if (!this.tabs.length || this.active == null) return;
    this.activate((this.active + delta + this.tabs.length) % this.tabs.length);
  }

  /** Unsaved files under `prefix` (for "trash" confirmations). */
  dirtyUnder(prefix: string): string[] {
    return [...this.docs].filter(([p, d]) => d.dirty && isUnder(p, prefix)).map(([p]) => p);
  }

  /** Tabs follow a rename of a file or of a folder above them. */
  renamed(from: string, to: string) {
    if (!this.tabs.some((t) => isUnder(t.path, from))) return; // idempotent: own op and its event
    const move = (p: string) => rebase(p, from, to);
    for (const t of this.tabs) t.path = move(t.path);
    this.docs = new Map([...this.docs].map(([p, d]) => {
      if (d.file) d.file = { ...d.file, path: move(d.file.path) };
      if (move(p) !== p) d.deleted = false;
      return [move(p), d];
    }));
    this.shown = null;
    this.tabsChanged();
  }

  /** Close tabs of trashed files (no prompt: the user already confirmed). */
  removed(paths: string[]) {
    const gone = (p: string) => paths.some((x) => isUnder(p, x));
    const before = this.tabs.length;
    const cur = this.activeTab;
    this.tabs = this.tabs.filter((t) => !gone(t.path));
    for (const p of [...this.docs.keys()]) if (gone(p)) this.docs.delete(p);
    if (this.tabs.length === before) return;
    const i = cur ? this.tabs.indexOf(cur) : -1;
    this.active = this.tabs.length ? (i >= 0 ? i : Math.min(this.active ?? 0, this.tabs.length - 1)) : null;
    this.shown = null;
    this.tabsChanged();
  }

  /** Files changed on disk (etag now, null = gone): clean tabs reload, dirty tabs get a
   *  banner; our own save carries the etag we already hold, so it changes nothing. */
  diskChanged(files: Stamp[]) {
    for (const { path, etag } of files) {
      const d = this.docs.get(path);
      if (!d?.file || etag === d.file.etag) continue;
      if (etag === null) this.redraw(path, () => { d.deleted = true; });
      else if (d.dirty) this.redraw(path, () => { d.diskChanged = true; });
      else void this.revalidate(path);
    }
  }

  /** Forget cached contents except unsaved ones (refresh button). */
  reloadAll() {
    for (const [p, d] of this.docs) if (!d.dirty) this.docs.delete(p);
    this.reshow();
  }

  get hasDirty() { return [...this.stores.values()].some((m) => [...m.values()].some((d) => d.dirty || d.saving)); }

  /** The shown pane's host (null: this Mac); call before setTabs of that pane. */
  setHost(host: string | null) {
    if ((host ?? "") === this.host) return;
    this.remember();          // the editor's state belongs to the old host's document
    this.host = host ?? "";
    this.shown = null;        // the same path on the new host is another document
    this.tabs = []; this.active = null;
    this.anchors.clear();
    this.tabsChanged();      // identical path lists on two hosts still need a fresh read
  }

  /** fbd was away: re-check every open tab (unchanged files answer 304). */
  recheck() { for (const t of this.tabs) void this.revalidate(t.path); }

  private get activeTab(): Tab | null { return this.active != null ? this.tabs[this.active] ?? null : null; }
  private activePath() { return this.activeTab?.path ?? null; }

  /** Show the active tab again from scratch (its file or view changed). */
  private reshow() { this.shown = null; void this.show(); }

  /** The tab strip changed: draw it and the active tab, and persist. */
  private tabsChanged() { this.renderTabs(); void this.show(); this.cb.changed(); }

  /** Apply `update` to `path` and show it again, keeping the scroll position. The screen
   *  is remembered first, so the update is not overwritten by the editor's old state. */
  private redraw(path: string, update?: () => void, d = this.docs.get(path)) {
    const onScreen = this.activePath() === path && this.docs.get(path) === d;
    if (onScreen) this.remember();
    update?.();
    if (onScreen) this.reshow();
  }

  private renderTabs() {
    this.cb.layout(this.tabs.length > 0);
    this.tabsEl.innerHTML = this.tabs.map((t, i) => {
      const dirty = this.docs.get(t.path)?.dirty;
      return `<div class="tab${i === this.active ? " active" : ""}${dirty ? " dirty" : ""}" data-i="${i}" title="${esc(t.path)}">` +
        `${fileIcon(basename(t.path))}<span class="tname">${esc(basename(t.path))}</span>` +
        `<span class="close" title="Close">${dirty ? "●" : "×"}</span></div>`;
    }).join("");
    this.tabsEl.querySelector(".tab.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
    this.cb.active?.(this.activePath());
  }

  // ── content ─────────────────────────────────────────────────────────────────

  private doc(path: string): Doc {
    let d = this.docs.get(path);
    if (!d) { d = { dirty: false, diskChanged: false, scroll: {}, host: this.host || null }; this.docs.set(path, d); }
    return d;
  }

  private load(path: string): Promise<void> {
    const d = this.doc(path);
    if (!d.loading) {
      d.loading = api<FileView>("GET", "/api/file", { query: { path }, host: d.host }).then(
        (f) => { d.file = f; d.error = undefined; d.state = undefined; d.saved = undefined; },
        (e) => { d.error = e.message; });
    }
    return d.loading;
  }

  /** Check disk state without replacing edits; 304 (same etag) costs no content. */
  private async revalidate(path: string, d = this.docs.get(path)) {
    if (!d?.file) return;
    path = d.file.path;
    try {
      const f = await api<FileView | undefined>("GET", "/api/file", { query: { path }, host: d.host, headers: { "If-None-Match": `"${d.file.etag}"` } });
      if (d.file?.path !== path) return;
      if (!f && !d.deleted) return; // 304: unchanged
      this.redraw(path, () => {
        d.deleted = false;
        if (f && d.dirty && f.etag !== d.file?.etag) d.diskChanged = true;
        if (f && !d.dirty) { d.file = f; d.state = undefined; d.saved = undefined; }
      }, d);
    } catch (e) {
      if (d.file?.path !== path) return;
      // keep the content: the file may have been renamed (the rename event can arrive first)
      if (e instanceof ApiError && e.status === 404 && e.code !== "no_agent" && !d.deleted) this.redraw(path, () => { d.deleted = true; }, d);
    }
  }

  /** Save the scroll position (and editor state) of what is on screen. */
  private remember() {
    if (!this.shown) return;
    const { path, mode } = this.shown;
    const d = this.docs.get(path);
    if (!d) return;
    if (mode === "source" && this.editor?.dom.isConnected) {
      d.scroll.source = this.editor.scrollDOM.scrollTop;
      d.state = this.editor.state;
    } else {
      const sc = this.body.querySelector<HTMLElement>(".scroll");
      if (sc) d.scroll[mode] = sc.scrollTop;
    }
  }

  private modeOf(tab: Tab) {
    return isRenderable(tab.path) && tab.view !== "source" ? "rendered" : "source";
  }

  private async show() {
    const render = ++this.render;
    const tab = this.activeTab;
    if (!tab) { this.body.innerHTML = ""; this.tools.innerHTML = ""; this.shown = null; return; }
    const mode = this.modeOf(tab);
    const d = this.doc(tab.path);
    if (!d.file && !d.error) {
      this.body.innerHTML = '<div class="pinfo"><span class="spin"></span> loading…</div>';
      this.tools.innerHTML = "";
      this.shown = null;
      await this.load(tab.path);
      if (render !== this.render || this.activeTab !== tab || this.docs.get(tab.path) !== d) return;
    }
    this.shown = { path: tab.path, mode };
    this.renderTools(tab, d);
    if (d.error || !d.file) {
      this.body.innerHTML = `<div class="pinfo">⚠ ${esc(d.error ?? "unavailable")}` +
        `<div><button class="btn" data-close-tab>Close tab</button> <button class="btn" data-retry>Retry</button></div></div>`;
      return;
    }
    const f = d.file;
    const banner = this.banner(f, d);
    if (f.binary) {
      if (f.mime?.startsWith("image/")) {
        this.body.innerHTML = `${banner}<div class="scroll imgview"><img src="${esc(rawUrl(f.path))}" alt=""><div class="imgmeta"></div></div>`;
        const img = this.body.querySelector("img")!;
        img.onload = () => { this.body.querySelector(".imgmeta")!.textContent = `${img.naturalWidth} × ${img.naturalHeight} · ${fmtSize(f.size)}`; };
      } else {
        this.body.innerHTML = `${banner}<div class="pinfo"><div class="big">Binary file</div>${esc(f.mime ?? "unknown type")} · ${fmtSize(f.size)}` +
          `<div><button class="btn" data-open-app>Open with default app</button></div></div>`;
      }
      return;
    }
    if (mode === "rendered") {
      this.body.innerHTML = `${banner}<div class="scroll doc"></div>`;
      const box = this.body.querySelector<HTMLElement>(".scroll")!;
      const anchor = this.anchors.get(f.path);
      this.anchors.delete(f.path);
      renderDoc(box, d.state ? d.state.doc.toString() : f.text ?? "", f.path, this.linksFor(d, render), anchor);
      if (!anchor) box.scrollTop = d.scroll.rendered ?? 0;
      return;
    }
    await this.showEditor(tab.path, d, banner, render);
  }

  private banner(f: FileView, d: Doc) {
    if (d.deleted) return `<div class="banner">Deleted on disk — Save recreates it${f.writable ? "" : " (read-only here)"}</div>`;
    if (d.diskChanged)
      return `<div class="banner">Changed on disk <button class="btn sm" data-disk="reload">Reload</button> <button class="btn sm" data-disk="keep">Keep mine</button></div>`;
    const notes: string[] = [];
    if (f.truncated) notes.push(`${fmtSize(f.size)} — showing first 1 MB, editing disabled`);
    else if (!f.binary && !f.utf8) notes.push("Not UTF-8 — editing disabled to avoid corruption");
    else if (!f.binary && !f.writable) notes.push("Read-only: outside writable folders or no permission");
    return notes.length ? `<div class="banner">${notes.map(esc).join(" · ")}</div>` : "";
  }

  private async showEditor(path: string, d: Doc, banner: string, render: number) {
    const f = d.file!;
    this.body.innerHTML = `${banner}<div class="cm-host"></div>`;
    const host = this.body.querySelector<HTMLElement>(".cm-host")!;
    if (!d.state) {
      d.state = EditorState.create({ doc: f.text ?? "", extensions: editorExtensions(path, f, this.lang, () => { const p = this.activePath(); if (p) void this.save(p); }) });
      d.saved = d.state.doc;
    }
    if (!this.editor) {
      this.editor = new EditorView({
        state: d.state, parent: host,
        dispatchTransactions: (trs, view) => {
          view.update(trs);
          if (!trs.some((t) => t.docChanged)) return;
          const cur = this.shown?.path;
          const doc = cur ? this.docs.get(cur) : null;
          if (!doc) return;
          const dirty = !!doc.saved && !view.state.doc.eq(doc.saved);
          doc.state = view.state;
          if (dirty !== doc.dirty) { doc.dirty = dirty; this.renderTabs(); this.renderTools(this.tabs[this.active!], doc); }
        },
      });
    } else { this.editor.setState(d.state); host.append(this.editor.dom); }
    const top = d.scroll.source ?? 0;
    requestAnimationFrame(() => { if (render === this.render && this.editor) this.editor.scrollDOM.scrollTop = top; });
    const lang = await languageForFile(basename(path)).catch(() => null);
    if (lang && render === this.render && this.docs.get(path) === d && this.editor && this.shown?.path === path && this.shown.mode === "source" && this.editor.state === d.state) {
      this.editor.dispatch({ effects: this.lang.reconfigure(lang) });
      d.state = this.editor.state;
    }
  }


  /** Save the file (If-Match etag); on conflict ask Overwrite / Reload / Cancel. */
  save(path: string, force = false, d = this.docs.get(path)): Promise<boolean> {
    if (!d) return Promise.resolve(false);
    if (!d.saving) d.saving = this.doSave(path, d, force).finally(() => { d.saving = undefined; });
    return d.saving;
  }

  private async doSave(path: string, d: Doc, force: boolean): Promise<boolean> {
    if (!d.file || !d.state || !d.file.writable) return false;
    path = d.file.path;                // a rename may have happened during a conflict prompt
    const doc = d.state.doc;           // what we send; typing may continue meanwhile
    const text = d.state.sliceDoc();
    try {
      const r = await api<{ etag: string }>("PUT", "/api/file", {
        query: { path }, body: { text }, host: d.host, headers: { "If-Match": `"${force ? "*" : d.file.etag}"` },
      });
      if (d.file.path !== path) {
        await this.revalidate(d.file.path, d);
        toast("Not saved: file was renamed — save again");
        return false;
      }
      d.file = { ...d.file, etag: r.etag, size: new TextEncoder().encode(text).length, text };
      d.saved = doc;
      d.dirty = !d.state.doc.eq(doc);  // edits typed during the request stay unsaved
      d.diskChanged = false;
      this.renderTabs();
      this.redraw(path, undefined, d);
      toast(`Saved ${basename(path)}`);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.code === "conflict") {
        const choice = await ask(e.message, "The file was changed by another program since you opened it.", [
          { id: "overwrite", label: "Overwrite", danger: true },
          { id: "reload", label: "Reload (discard mine)" },
          { id: "cancel", label: "Cancel", primary: true },
        ]);
        if (choice === "overwrite") return this.doSave(path, d, true);
        if (choice === "reload") { this.discard(path, d); return false; }
        return false;
      }
      toast(e instanceof ApiError && e.status === 0 ? "Not saved: backend restarting — save again" : e instanceof Error ? e.message : String(e));
      return false;
    }
  }

  private discard(path: string, d = this.docs.get(path)) {
    if (!d) return;
    path = d.file?.path ?? path;
    d.dirty = false; d.diskChanged = false; d.loading = undefined; d.file = undefined; d.state = undefined;
    if (this.docs.get(path) !== d) return;
    this.renderTabs();
    if (this.activePath() === path) this.reshow();
  }

  private renderTools(tab: Tab, d: Doc) {
    const f = d.file;
    const md = isRenderable(tab.path) && f && !f.binary;
    const mode = this.modeOf(tab);
    this.tools.innerHTML =
      (d.dirty ? `<button class="btn sm primary" data-act="save" title="Save (⌘S)">Save</button>` : "") +
      (md ? `<div class="seg"><button data-act="rendered" class="${mode === "rendered" ? "on" : ""}" title="Rendered">Rendered</button>` +
        `<button data-act="source" class="${mode === "source" ? "on" : ""}" title="Source${f?.writable ? " (editable)" : ""}">Source</button></div>` : "") +
      `<button class="icon" data-act="reveal" title="Reveal in tree"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="2.5"/><path d="M8 1.5v3M8 11.5v3M1.5 8h3M11.5 8h3"/></svg></button>` +
      `<button class="icon" data-act="open-app" title="Open with default app"><svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 2.5h4.5V7M13.5 2.5 7 9M12 10v3.5H2.5V4H6"/></svg></button>`;
  }

  private openWithApp(path: string) { return apiOrToast("POST", "/api/os/open", { body: { path } }); }

  /** Buttons in banners and cards; links inside rendered Markdown never navigate the panel. */
  private async onBodyClick(e: MouseEvent) {
    const t = e.target as HTMLElement;
    const path = this.activePath();
    if (!path) return;
    if (t.closest("[data-open-app]")) return void this.openWithApp(path);
    if (t.closest("[data-close-tab]") && this.active != null) return void this.close(this.active);
    if (t.closest("[data-retry]")) { this.docs.delete(path); return this.reshow(); }
    const disk = t.closest<HTMLElement>("[data-disk]");
    if (disk) {
      if (disk.dataset.disk === "reload") this.discard(path);
      else this.redraw(path, () => { this.doc(path).diskChanged = false; });
      return;
    }
  }
}
