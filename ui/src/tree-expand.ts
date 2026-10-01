// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// "Expand all" (AC-31): breadth-first over real folders below a start folder, bounded so
// a big tree stays responsive. The tree supplies the I/O; this module decides.

import { join } from "./paths";

/** Build output, dependencies and VCS data: expanding them buries the source. */
export const SKIP = new Set([
  "node_modules", ".git", "target", "dist", "build", ".venv", "venv", "__pycache__",
  ".next", ".cache", "Pods", "DerivedData",
]);
export const MAX_FOLDERS = 200;
export const MAX_DEPTH = 8;
/** Larger folders stay collapsed: their subfolders would need every page of the listing. */
export const MAX_ENTRIES = 500;
const PARALLEL = 6;

export interface Listing { total: number; rows: { n: string; k: string }[] }

export interface ExpandOps {
  /** Expand `path` and return its first page (null: unreadable, gone, or cancelled). */
  expand(path: string): Promise<Listing | null>;
  collapse(path: string): void;
  /** False once the user re-roots, collapses or starts another expand: stop at once. */
  alive(): boolean;
}

export interface ExpandResult {
  expanded: number;
  skipped: string[];      // names from SKIP, in the order met
  tooLarge: number;
  depthLimit: boolean;
  folderLimit: boolean;
  cancelled: boolean;
}

/** Expand every folder below `start` (itself included unless it is `root`, always shown). */
export async function expandAll(start: string, root: string, ops: ExpandOps): Promise<ExpandResult> {
  const res: ExpandResult = { expanded: 0, skipped: [], tooLarge: 0, depthLimit: false, folderLimit: false, cancelled: false };
  let level = [start];
  for (let depth = 0; level.length; depth++) {
    const next: string[] = [];
    for (let i = 0; i < level.length; i += PARALLEL) {
      if (!ops.alive()) return { ...res, cancelled: true };
      const batch = level.slice(i, i + PARALLEL);
      const listings = await Promise.all(batch.map((p) => ops.expand(p)));
      if (!ops.alive()) return { ...res, cancelled: true };
      batch.forEach((p, j) => {
        const l = listings[j];
        if (p !== root && l) res.expanded++;
        if (!l) return;                                     // its error row says why
        if (l.total > MAX_ENTRIES) {
          res.tooLarge++;
          if (p !== root) { ops.collapse(p); res.expanded--; }
          return;
        }
        for (const r of l.rows) {
          if (r.k !== "d") continue;                        // files, and symlinks (no cycles)
          if (SKIP.has(r.n)) { if (!res.skipped.includes(r.n)) res.skipped.push(r.n); continue; }
          next.push(join(p, r.n));
        }
      });
    }
    if (next.length && depth + 1 > MAX_DEPTH) { res.depthLimit = true; break; }
    const room = MAX_FOLDERS - res.expanded;
    if (next.length > room) { res.folderLimit = true; next.length = Math.max(0, room); }
    level = next;
  }
  return res;
}

export function summary(r: ExpandResult): string {
  const parts = [`Expanded ${r.expanded} folder${r.expanded === 1 ? "" : "s"}`];
  const why = [...r.skipped, ...(r.tooLarge ? [`${r.tooLarge} too large`] : [])];
  if (why.length) parts.push(`skipped ${r.skipped.length + r.tooLarge} (${why.join(", ")})`);
  if (r.depthLimit) parts.push(`depth limit ${MAX_DEPTH}`);
  if (r.folderLimit) parts.push(`limit ${MAX_FOLDERS} folders`);
  return parts.join(" · ");
}
