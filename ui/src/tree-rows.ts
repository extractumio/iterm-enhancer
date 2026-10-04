// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Row arithmetic of the virtual tree: a folder's row count is its total plus the row counts
// of its expanded children (plus one row while a new item is being named), memoized per
// structural version; rows are located by walking expanded children in index order.

import { basename, dirname, type Row } from "./api";

export const PAGE = 500;

export interface DirNode {
  path: string;
  loaded: boolean;
  writable: boolean;
  error?: string;
  total: number;
  gen: number;
  pages: Map<number, Row[]>;
  pending: Set<number>;
  read?: number; // invalidates requests started before a forced refresh
  /** expanded children with their index in this folder's (filtered) listing */
  children: Map<string, { idx: number; node: DirNode }>;
  sorted?: { idx: number; node: DirNode }[];
  memo?: { v: number; rows: number };
}

export type RowDesc =
  | { kind: "entry"; dir: DirNode; idx: number; depth: number; row?: Row }
  | { kind: "note"; dir: DirNode; depth: number; text: string; spin?: boolean }
  | { kind: "edit"; dir: DirNode; depth: number };

/** What the arithmetic reads from the tree. */
export interface RowsHost {
  readonly version: number;
  readonly filter: string;
  readonly root: DirNode | null;
  readonly nodes: Map<string, DirNode>;
  creatingIn(path: string): number;
  load(n: DirNode, page: number): void;
}

export function rowsOf(n: DirNode, h: RowsHost): number {
  if (n.memo?.v === h.version) return n.memo.rows;
  const extra = h.creatingIn(n.path);
  let rows = 1 + extra;
  if (n.loaded && !n.error && n.total > 0) {
    rows = n.total + extra;
    for (const c of n.children.values()) rows += rowsOf(c.node, h);
  }
  n.memo = { v: h.version, rows };
  return rows;
}

export function rowAt(n: DirNode, i: number, depth: number, h: RowsHost): RowDesc {
  if (h.creatingIn(n.path)) {
    if (i === 0) return { kind: "edit", dir: n, depth };
    i--;
  }
  if (!n.loaded) return { kind: "note", dir: n, depth, text: "reading…", spin: true };
  if (n.error) return { kind: "note", dir: n, depth, text: `⚠ ${n.error}` };
  if (n.total === 0) return { kind: "note", dir: n, depth, text: h.filter ? "no matches" : "empty" };
  let pos = i, consumed = 0;
  n.sorted ??= [...n.children.values()].sort((a, b) => a.idx - b.idx);
  for (const c of n.sorted) {
    const block = c.idx - consumed + 1;
    if (pos < block) return entry(n, consumed + pos, depth, h);
    pos -= block;
    consumed = c.idx + 1;
    const sub = rowsOf(c.node, h);
    if (pos < sub) return rowAt(c.node, pos, depth + 1, h);
    pos -= sub;
  }
  return entry(n, consumed + pos, depth, h);
}

function entry(n: DirNode, idx: number, depth: number, h: RowsHost): RowDesc {
  // a stale child index can point past the end until relocate() runs: never fetch there
  if (idx >= n.total) return { kind: "note", dir: n, depth, text: "" };
  const page = Math.floor(idx / PAGE);
  const row = n.pages.get(page)?.[idx - page * PAGE];
  if (!row && !n.pending.has(page)) h.load(n, page);
  return { kind: "entry", dir: n, idx, depth, row };
}

/** Global row index of entry `idx` in folder `n`, or -1 if an ancestor is collapsed. */
export function indexOf(n: DirNode, idx: number, h: RowsHost): number {
  let base = 0;
  if (n !== h.root) {
    const parent = h.nodes.get(dirname(n.path));
    const me = parent?.children.get(basename(n.path));
    const at = parent && me ? indexOf(parent, me.idx, h) : -1;
    if (at < 0) return -1;
    base = at + 1;
  }
  let offset = idx + h.creatingIn(n.path);
  for (const c of n.children.values()) if (c.idx < idx) offset += rowsOf(c.node, h);
  return base + offset;
}
