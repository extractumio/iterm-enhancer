// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Icons. File-type and folder glyphs are Tabler Icons 3.48.0 (outline), copied unchanged
// into src/icons/ with their MIT license, inlined at build time and tinted through CSS
// variables so they follow the terminal theme (AC-32).
/*! Tabler Icons 3.48.0 | MIT License | Copyright (c) 2020-2026 Paweł Kuna | https://tabler.io/icons */

import braces from "./icons/braces.svg";
import binary from "./icons/binary.svg";
import copy from "./icons/copy.svg";
import database from "./icons/database.svg";
import file from "./icons/file.svg";
import fileCode from "./icons/file-code.svg";
import fileText from "./icons/file-text.svg";
import fileZip from "./icons/file-zip.svg";
import folder from "./icons/folder.svg";
import folderOpen from "./icons/folder-open.svg";
import gitBranch from "./icons/git-branch.svg";
import key from "./icons/key.svg";
import lock from "./icons/lock.svg";
import markdown from "./icons/markdown.svg";
import movie from "./icons/movie.svg";
import music from "./icons/music.svg";
import pdf from "./icons/file-type-pdf.svg";
import photo from "./icons/photo.svg";
import settings from "./icons/settings.svg";
import table from "./icons/table.svg";
import terminal from "./icons/terminal-2.svg";
import typography from "./icons/typography.svg";
import web from "./icons/world-www.svg";

export const chevron =
  '<svg width="8" height="8" viewBox="0 0 8 8"><path d="M2 1l4 3-4 3" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';

export const ICONS = {
  code: fileCode, terminal, braces, settings, markdown, text: fileText, table, photo, pdf,
  zip: fileZip, web, lock, git: gitBranch, key, database, music, movie, font: typography,
  binary, file, folder, folderOpen,
} as const;
export type IconName = keyof typeof ICONS;
type Color = "code" | "conf" | "doc" | "img" | "arch" | "web" | "faint";

/** Whole names first (they beat extensions), then extensions; first match wins. */
const BY_NAME: [RegExp, IconName, Color][] = [
  [/^(Makefile|GNUmakefile|Dockerfile|Containerfile|Procfile|Justfile|\.editorconfig|\.npmrc|\.nvmrc|\.env(\..+)?)$/, "settings", "conf"],
  [/^\.git(ignore|attributes|modules|keep)$/, "git", "conf"],
  [/^id_(rsa|dsa|ecdsa|ed25519)$/, "key", "conf"],
  [/^(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|bun\.lockb?|Gemfile\.lock|poetry\.lock|composer\.lock)$/, "lock", "conf"],
];
const BY_EXT: [RegExp, IconName, Color][] = [
  [/\.(sh|bash|zsh|fish|ksh|command|ps1)$/i, "terminal", "code"],
  [/\.(sql|sqlite3?|db)$/i, "database", "code"],
  [/\.(js|mjs|cjs|ts|mts|cts|tsx|jsx|py|rs|go|c|h|cc|cpp|hpp|m|mm|java|kt|swift|rb|php|lua|pl|scala|cs|dart|zig|ex|exs|erl|hs|ml|clj|r|vue|svelte)$/i, "code", "code"],
  [/\.(json|jsonc|json5|ipynb)$/i, "braces", "conf"],
  [/\.lock$/i, "lock", "conf"],
  [/\.(toml|ya?ml|ini|conf|cfg|properties|plist|xml|env)$/i, "settings", "conf"],
  [/\.(pem|key|crt|cer|p12|pfx|pub|gpg|asc)$/i, "key", "conf"],
  [/\.(md|markdown|mdx)$/i, "markdown", "doc"],
  [/\.(csv|tsv|xlsx?|numbers|ods)$/i, "table", "doc"],
  [/\.(txt|rst|log|adoc|rtf|org)$/i, "text", "doc"],
  [/\.(ttf|otf|woff2?|eot)$/i, "font", "doc"],
  [/\.pdf$/i, "pdf", "img"],
  [/\.(png|jpe?g|gif|svg|webp|ico|icns|heic|bmp|tiff?|avif|psd)$/i, "photo", "img"],
  [/\.(mp3|wav|flac|aac|ogg|m4a|aiff?)$/i, "music", "img"],
  [/\.(mp4|mov|mkv|webm|avi|m4v)$/i, "movie", "img"],
  [/\.(zip|gz|tgz|bz2|xz|7z|tar|dmg|iso|rar|zst|jar|whl)$/i, "zip", "arch"],
  [/\.(html?|css|scss|sass|less)$/i, "web", "web"],
  [/\.(out|so|dylib|o|a|exe|bin|class|wasm|pyc|dll)$/i, "binary", "faint"],
];

/** The icon and theme color of a file name. */
export function iconFor(name: string): { icon: IconName; color: Color } {
  const hit = BY_NAME.find(([re]) => re.test(name)) ?? BY_EXT.find(([re]) => re.test(name));
  return hit ? { icon: hit[1], color: hit[2] } : { icon: "file", color: "faint" };
}

/** A Tabler SVG as a row icon: our size class, our color, a lighter stroke at 15 px. */
function tint(svg: string, color: string): string {
  return svg
    .replace(/\s+/g, " ")
    .replace(/<svg[^>]*>/, `<svg class="ico" style="color:${color}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">`)
    .replace(/<path stroke="none" d="M0 0h24v24H0z" fill="none"\s*\/>/, "");
}

const COLOR: Record<Color, string> = {
  code: "var(--k-code)", conf: "var(--k-conf)", doc: "var(--k-doc)", img: "var(--k-img)",
  arch: "var(--k-arch)", web: "var(--k-web)", faint: "var(--fg-faint)",
};
const cache = new Map<string, string>();
const tinted = (icon: IconName, color: string) => {
  const k = icon + color;
  let s = cache.get(k);
  if (!s) cache.set(k, (s = tint(ICONS[icon], color)));
  return s;
};

export const fileIcon = (name: string) => { const { icon, color } = iconFor(name); return tinted(icon, COLOR[color]); };
export const folderIcon = (open: boolean) => tinted(open ? "folderOpen" : "folder", "var(--folder)");
export const copyIcon = tint(copy, "currentColor").replace('class="ico"', 'width="14" height="14"');
