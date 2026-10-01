// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// HTML for one tree row. Pure: the tree passes in what the row needs to know.

import { esc, fmtSize, isDirKind, join, type Row } from "./api";
import { chevron, fileIcon, folderIcon } from "./icons";

export type RowView =
  | { kind: "entry"; dir: string; depth: number; row?: Row }
  | { kind: "note"; depth: number; text: string; spin?: boolean }
  | { kind: "edit"; depth: number; folder: boolean; name: string };

export interface RowState {
  expanded: Set<string>;
  selected: Set<string>;
  cursor: number;
  filter: string;
  renaming(path: string): boolean;
}

export function rowHtml(d: RowView, i: number, s: RowState): string {
  const guides = `<span class="guides">${"<i></i>".repeat(d.depth)}</span>`;
  if (d.kind === "edit")
    return `<div class="row editing" data-i="${i}">${guides}<span class="chev"></span>${d.folder ? folderIcon(false) : fileIcon(d.name)}</div>`;
  if (d.kind === "note")
    return `<div class="row note" data-i="${i}">${guides}<span class="chev"></span>${d.spin ? '<span class="spin"></span>' : ""}<span class="muted">${esc(d.text)}</span></div>`;
  if (!d.row)
    return `<div class="row" data-i="${i}">${guides}<span class="chev"></span><span class="skeleton"></span></div>`;
  const r = d.row, p = join(d.dir, r.n), isDir = isDirKind(r.k);
  const open = isDir && s.expanded.has(p);
  const renaming = s.renaming(p);
  const cls = ["row", s.selected.has(p) ? "sel" : "", i === s.cursor ? "cursor" : "", renaming ? "editing" : ""].join(" ");
  return `<div class="${cls}" data-i="${i}" data-p="${esc(p)}" data-d="${isDir ? 1 : 0}" title="${esc(r.n)}">${guides}` +
    `<span class="chev${open ? " open" : ""}">${isDir ? chevron : ""}</span>` +
    (isDir ? folderIcon(open) : fileIcon(r.n)) +
    (renaming ? "" : `<span class="name${isDir ? " dir" : ""}${r.n.startsWith(".") ? " hidden" : ""}">${highlight(r.n, s.filter)}</span>` +
      (r.k === "l" || r.k === "L" ? '<span class="link" title="symlink">↪</span>' : "") +
      (isDir ? "" : `<span class="size">${fmtSize(r.s)}</span>`)) + "</div>";
}

function highlight(name: string, filter: string) {
  const i = filter ? name.toLowerCase().indexOf(filter.toLowerCase()) : -1;
  if (i < 0) return esc(name);
  const j = i + filter.length;
  return esc(name.slice(0, i)) + "<mark>" + esc(name.slice(i, j)) + "</mark>" + esc(name.slice(j));
}
