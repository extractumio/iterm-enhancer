// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// POSIX path and URL helpers. No DOM and no token: modules tested under node import these.

export const isUnder = (p: string, dir: string) => p === dir || p.startsWith(dir + "/");
/** `p` after `from` was renamed to `to` (unchanged if not inside `from`). */
export const rebase = (p: string, from: string, to: string) => (isUnder(p, from) ? to + p.slice(from.length) : p);

export const basename = (p: string) => p.slice(p.lastIndexOf("/") + 1) || "/";
export const dirname = (p: string) => p.slice(0, Math.max(p.lastIndexOf("/"), 1));
export const join = (a: string, b: string) => (a === "/" ? "/" + b : a + "/" + b);

/** Resolve a relative link against a folder, POSIX-style. */
export function resolvePath(dir: string, rel: string): string {
  const parts = (rel.startsWith("/") ? rel : dir + "/" + rel).split("/");
  const out: string[] = [];
  for (const p of parts) {
    if (!p || p === ".") continue;
    if (p === "..") out.pop(); else out.push(p);
  }
  return "/" + out.join("/");
}

/** A URL with a scheme (`https:`, `javascript:`, `file:` …), as opposed to a relative path. */
export const hasScheme = (u: string) => /^[a-z][a-z0-9+.-]*:/i.test(u);
