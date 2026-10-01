// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Inline naming in the tree: new file, new folder, rename. One persistent <input> floats
// over the row being edited (the rows themselves are re-rendered every frame).

import { basename } from "./api";

export type EditMode = "file" | "folder" | "rename";
export interface Edit { mode: EditMode; dir: string; path?: string; error?: string; busy?: boolean }

export interface EditHooks {
  commit(mode: EditMode, dir: string, name: string, path?: string): Promise<string | null>; // error or null
  rerender(): void;
  done(): void;                                                                             // focus back to the tree
}

export class InlineEdit {
  edit: Edit | null = null;
  readonly el: HTMLDivElement;
  private input: HTMLInputElement;
  private err: HTMLDivElement;

  constructor(private hooks: EditHooks) {
    this.el = document.createElement("div");
    this.el.className = "inline-edit";
    this.el.innerHTML = '<input spellcheck="false" autocomplete="off"><div class="err"></div>';
    this.input = this.el.querySelector("input")!;
    this.err = this.el.querySelector(".err")!;
    this.input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") { e.preventDefault(); void this.commit(); }
      if (e.key === "Escape") { e.preventDefault(); this.cancel(); }
    });
    this.input.addEventListener("input", () => { if (this.edit) { this.edit.error = undefined; this.showError(); } });
    this.input.addEventListener("blur", () => {
      // like an IDE: leaving the field commits a typed name, an empty field cancels
      setTimeout(() => {
        if (!this.edit || this.edit.busy || document.activeElement === this.input) return;
        if (this.input.value.trim() && this.input.value !== basename(this.edit.path ?? "")) void this.commit();
        else this.cancel();
      }, 120);
    });
  }

  get value() { return this.input.value; }

  /** 1 while a new item is being named inside `dir` (the tree adds a row for it). */
  creatingIn(dir: string) { return this.edit && this.edit.mode !== "rename" && this.edit.dir === dir ? 1 : 0; }
  renaming(path: string) { return this.edit?.mode === "rename" && this.edit.path === path; }

  start(edit: Edit) {
    this.edit = edit;
    const name = edit.path ? basename(edit.path) : "";
    this.input.value = name;
    this.hooks.rerender();
    requestAnimationFrame(() => {
      this.input.focus();
      const dot = name.lastIndexOf(".");
      if (name) this.input.setSelectionRange(0, dot > 0 ? dot : name.length); // the name without its extension
    });
  }

  cancel() {
    if (!this.edit) return;
    this.edit = null;
    this.el.classList.remove("on");
    this.hooks.rerender();
    this.hooks.done();
  }

  /** Place the field over row `row` at `depth`, or hide it when that row is not rendered. */
  place(row: number, depth: number, rowH: number) {
    if (!this.edit || row < 0) { this.el.classList.remove("on"); return; }
    this.el.classList.add("on");
    this.el.style.top = `${row * rowH}px`;
    this.el.style.left = `${6 + depth * 14 + 12 + 5 + 15 + 4}px`;
    this.el.style.height = `${rowH}px`;
    this.showError();
  }

  private showError() {
    this.err.textContent = this.edit?.error ?? "";
    this.el.classList.toggle("bad", !!this.edit?.error);
  }

  private async commit() {
    const e = this.edit;
    if (!e || e.busy) return;
    e.busy = true;
    const error = await this.hooks.commit(e.mode, e.dir, this.input.value.trim(), e.path);
    e.busy = false;
    if (this.edit !== e) return;
    if (error) {
      e.error = error;
      this.showError();
      this.input.focus();
    } else this.cancel();
  }
}
