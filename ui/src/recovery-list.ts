// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Which checkpoints the recovery dialog lists, which one it selects, and their labels
// (AC-50). Pure: no DOM, unit-tested in ui/test/recovery-list.test.mjs.

export interface Entry { id: string; captured: number; windows: number; panes: number; stable: boolean; epoch: string }
export interface Status {
  epoch?: string | null;
  entries: Entry[];
  startup: { snapshot: string | null; phase: string; skipped?: boolean } | null;
  job: { id: string; snapshot: string; status: string } | null;
}
export interface Item { id: string; label: string }
export interface Group { label: string; items: Item[] }

/** Stable checkpoints, the newest of each run, and the sources of startup and the job. */
export function visibleEntries(s: Status): Entry[] {
  const keep = new Set<string>();
  const newestOfRun = new Map<string, string>();
  for (const e of s.entries) newestOfRun.set(e.epoch, e.id);   // entries are oldest first
  for (const id of newestOfRun.values()) keep.add(id);
  if (s.startup?.snapshot) keep.add(s.startup.snapshot);
  if (s.job?.snapshot) keep.add(s.job.snapshot);
  return s.entries.filter((e) => e.stable || keep.has(e.id));
}

/** The pending startup source, else an unfinished job's, else the newest with a window. */
export function defaultSelection(s: Status): string {
  const has = (id?: string | null) => !!id && s.entries.some((e) => e.id === id);
  if (s.startup?.phase === "pending" && has(s.startup.snapshot)) return s.startup.snapshot!;
  if (s.job && s.job.status !== "complete" && has(s.job.snapshot)) return s.job.snapshot;
  const withWindows = [...s.entries].reverse().find((e) => e.windows > 0);
  return (withWindows ?? s.entries.at(-1))?.id ?? "";
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

function when(captured: number, now: Date): string {
  const d = new Date(captured * 1000);
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  if (d.getTime() >= day) return `Today ${time}`;
  if (d.getTime() >= day - 86_400_000) return `Yesterday ${time}`;
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}`;
}

/** The visible checkpoints, newest first, in "This iTerm2 run" and "Earlier iTerm2 runs". */
export function groups(s: Status, now = new Date()): Group[] {
  const current = s.epoch ?? s.entries.at(-1)?.epoch;
  const newest = s.entries.at(-1)?.id;
  const lastOfRun = new Map<string, string>();
  for (const e of s.entries) lastOfRun.set(e.epoch, e.id);
  const item = (e: Entry): Item => {
    const marks = [e.id === newest ? "latest" : "", e.epoch !== current && lastOfRun.get(e.epoch) === e.id ? "last before restart" : ""].filter(Boolean);
    return { id: e.id, label: [when(e.captured, now), `${plural(e.windows, "window")}, ${plural(e.panes, "pane")}`, ...marks].join(" · ") };
  };
  const visible = visibleEntries(s).reverse();
  return [
    { label: "This iTerm2 run", items: visible.filter((e) => e.epoch === current).map(item) },
    { label: "Earlier iTerm2 runs", items: visible.filter((e) => e.epoch !== current).map(item) },
  ].filter((g) => g.items.length);
}
