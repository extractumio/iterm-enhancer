// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The tree's context menu. In the web app (AC-53) the actions that act on the Mac itself
// (Finder, apps, a window there, typing into its terminal, enabling a host) are left out.

import { apiOrToast, dirname, PROXIED, scope, type TermState } from "./api";
import { hostEntries, hostMenu } from "./host-offer";
import { menu, type MenuEntry } from "./dialogs";
import type { Tree } from "./tree";

export interface MenuDeps {
  tree: Tree;
  term: () => TermState;
  switchedSince(host: string | null): boolean;
  open(path: string): void;
  openWindow(path: string): void;
  trash(): void;
  copy(text: string): void;
  relative(path: string): string;
  terminal(action: "insert" | "cd", body: object): unknown;
}

export async function contextMenu(path: string | null, isDir: boolean, ev: MouseEvent, d: MenuDeps) {
  const { tree } = d;
  const sel = path ? [...tree.selected] : [];
  const many = sel.length > 1;
  const target = path ?? tree.root?.path ?? "";
  const dir = path && !isDir ? dirname(path) : target;
  const canWrite = tree.isWritable(dir);
  const atMac = !PROXIED;                      // used at the Mac, not from the web app
  const macFiles = atMac && !scope;            // and this Mac's files: Finder and apps can take them
  const entries: MenuEntry[] = [
    { id: "new-file", label: "New File…", keys: "⌥N", disabled: !canWrite },
    { id: "new-folder", label: "New Folder…", keys: "⌥⇧N", disabled: !canWrite },
  ];
  if (path) entries.push(
    "-",
    ...(!isDir && !many ? [{ id: "open", label: "Open", keys: "↩" }, ...(atMac ? [{ id: "open-window", label: "Open in Window", keys: "⌘↩" }] : [])] : []),
    { id: "rename", label: "Rename…", keys: "F2", disabled: many || !tree.isWritable(dirname(path)) },
    { id: "trash", label: many ? `Move ${sel.length} Items to Trash` : "Move to Trash", keys: "⌘⌫", danger: true, disabled: !sel.every((p) => tree.isWritable(dirname(p))) },
    "-",
    { id: "copy-path", label: many ? `Copy ${sel.length} Paths` : "Copy Path", keys: "⌥⌘C" },
    { id: "copy-rel", label: many ? `Copy ${sel.length} Relative Paths` : "Copy Relative Path", keys: "⌥⇧⌘C" },
    ...(macFiles ? [{ id: "reveal", label: "Reveal in Finder", disabled: many }, { id: "open-app", label: "Open with Default App", disabled: many }] : []),
    ...(atMac ? ["-" as const, { id: "insert", label: "Insert Path in Terminal" }, { id: "cd", label: "Open Terminal Here", disabled: many }] : []),
  );
  else if (atMac) entries.push("-", ...(macFiles ? [{ id: "reveal", label: "Reveal in Finder" }] : []), { id: "cd", label: "Open Terminal Here" });
  const remote = d.term().remote;
  if (atMac) entries.push(...hostEntries(remote));
  const host = scope;
  const choice = await menu(ev.clientX, ev.clientY, entries);
  if (choice && d.switchedSince(host)) return;
  if (choice?.startsWith("host-") && remote) return void hostMenu(choice, remote);
  switch (choice) {
    case "new-file": return void tree.startCreate(dir, "file");
    case "new-folder": return void tree.startCreate(dir, "folder");
    case "open": return d.open(target);
    case "open-window": return d.openWindow(target);
    case "rename": return tree.startRename(target);
    case "trash": return d.trash();
    case "copy-path": return d.copy(sel.join("\n"));
    case "copy-rel": return d.copy(sel.map(d.relative).join("\n"));
    case "reveal": return void apiOrToast("POST", "/api/os/reveal", { body: { path: target } });
    case "open-app": return void apiOrToast("POST", "/api/os/open", { body: { path: target } });
    case "insert": return void d.terminal("insert", { paths: sel.map(d.relative) });
    case "cd": return void d.terminal("cd", { path: dir });
  }
}
