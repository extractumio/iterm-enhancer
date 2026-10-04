// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A stream gap affects the folders and documents of its own file host.
import type { Tree } from "./tree";
import type { Viewer } from "./viewer";

export function refreshScope(host: string | null, current: string | null, tree: Tree, viewer: Viewer) {
  if (host !== current) return;
  viewer.recheck();
  if (tree.root) void tree.refreshDirs([tree.root.path, ...tree.expanded]);
}
