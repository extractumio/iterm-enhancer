// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// File-operation replies update only the pane and host where the action started.

import { api, ApiError, basename, toast } from "./api";
import { ask } from "./dialogs";
import type { Tree, EditMode } from "./tree";
import type { Viewer } from "./viewer";

interface CommitHost {
  host: string | null;
  current(): boolean;
  tree: Tree;
  viewer: Viewer;
  changed(): void;
}

export async function commitPath(mode: EditMode, dir: string, name: string, path: string | undefined, ctx: CommitHost): Promise<string | null> {
  try {
    let created: string;
    if (mode === "rename") {
      if (!path || name === basename(path)) return null;
      created = (await api<{ path: string }>("POST", "/api/fs/rename", { body: { path, name }, host: ctx.host })).path;
      if (!ctx.current()) return null;
      ctx.tree.renamed(path, created);
      ctx.viewer.renamed(path, created);
    } else {
      created = (await api<{ path: string }>("POST", `/api/fs/${mode === "folder" ? "mkdir" : "touch"}`, { body: { parent: dir, name }, host: ctx.host })).path;
      if (!ctx.current()) return null;
      if (mode === "file") ctx.viewer.open(created);
    }
    await ctx.tree.refreshDirs([dir]);
    if (!ctx.current()) return null;
    ctx.tree.selected = new Set([created]);
    await ctx.tree.reveal(created);
    if (!ctx.current()) return null;
    ctx.changed();
    return null;
  } catch (e) {
    return ctx.current() ? (e instanceof Error ? e.message : String(e)) : null;
  }
}

export async function trashPaths(paths: string[], host: string | null, dirty: string[], current: () => boolean): Promise<string[] | null> {
  const what = paths.length === 1 ? `"${basename(paths[0])}"` : `${paths.length} items`;
  const extra = dirty.length ? `${dirty.length} unsaved file${dirty.length > 1 ? "s" : ""} will be lost. ` : "";
  const ok = await ask(`Move ${what} to Trash?`, `${extra}You can restore from the Trash in Finder.`,
    [{ id: "trash", label: "Move to Trash", danger: true, primary: true }, { id: "cancel", label: "Cancel" }]);
  if (ok !== "trash" || !current()) return null;
  type Reply = { failed: { path: string; message: string }[] };
  let r: Reply;
  try {
    r = await api<Reply>("POST", "/api/fs/trash", { body: { paths }, host });
  } catch (e) {
    if (!(e instanceof ApiError) || !e.body?.failed) {
      toast(e instanceof Error ? e.message : String(e));
      return null;
    }
    r = e.body;
  }
  if (r.failed.length) toast(r.failed.map((f) => f.message).join("; "));
  return paths.filter((p) => !r.failed.some((f) => f.path === p));
}
