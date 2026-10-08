// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Web addresses and file paths in terminal text (AC-52): which one a click is on, and the
// absolute path a printed path names. Pure functions; the page decides what opening means.

// Extensions a printed name is taken as a file by when it has no slash.
const EXTENSIONS = new Set(`
  md markdown txt rst adoc log csv tsv json jsonc json5 yaml yml toml ini cfg conf env properties xml plist
  html htm css scss sass less svg js mjs cjs jsx ts mts cts tsx vue svelte astro
  py pyi rb php pl pm lua r jl go rs c h cc cpp cxx hpp hh m mm swift kt kts java scala clj ex exs erl hs ml
  cs fs vb dart zig nim sh bash zsh fish ps1 bat cmd sql graphql gql proto tf hcl nix lock gradle mk cmake
  dockerfile png jpg jpeg gif webp ico bmp tiff pdf ipynb patch diff
`.trim().split(/\s+/));
const NAMED = new Set(["Makefile", "Dockerfile", "Gemfile", "Rakefile", "Procfile", "LICENSE", "README", "CHANGELOG", "Justfile"]);

const URL_RE = /\bhttps?:\/\/[^\s<>"'`]+/g;
const PATH_CHARS = /[\w.~/@+\-%=,:]/;        // what a printed path is made of (no spaces, quotes or brackets)
const TRAIL = /[.,;:!?)\]}>'"`]+$/;           // sentence punctuation after an address or a path

/** The web address or file path at `offset` of `text`, or null: {kind: "url"|"path", value, line?}. */
export function tokenAt(text, offset) {
  for (const m of text.matchAll(URL_RE)) {
    const value = m[0].replace(TRAIL, "");
    if (offset >= m.index && offset < m.index + value.length) return { kind: "url", value };
  }
  if (offset < 0 || offset >= text.length || !PATH_CHARS.test(text[offset])) return null;
  let a = offset, b = offset;
  while (a > 0 && PATH_CHARS.test(text[a - 1])) a--;
  while (b < text.length && PATH_CHARS.test(text[b])) b++;
  let word = text.slice(a, b).replace(TRAIL, "").replace(/^[:=,]+/, "");
  let line = null;
  const at = word.match(/^(.*?):(\d+)(?::\d+)?$/);           // path:line or path:line:col
  if (at) { word = at[1]; line = Number(at[2]); }
  if (!looksLikePath(word)) return null;
  return line ? { kind: "path", value: word, line } : { kind: "path", value: word };
}

// A path is anchored (/, ~/, ./, ../) or ends in a known name or extension; a slash alone is
// not enough ("and/or", 10/07/2026), nor a number or a version.
function looksLikePath(w) {
  if (!w || w.length > 1024 || w.includes("://")) return false;
  const name = w.split("/").pop();
  if (!name || /^[\d.:,-]+$/.test(name)) return false;
  if (/^(\/|~\/|\.\.?\/)/.test(w)) return true;
  if (NAMED.has(name)) return true;
  const dot = name.lastIndexOf(".");
  return dot > 0 && EXTENSIONS.has(name.slice(dot + 1).toLowerCase());
}

/** The absolute path a printed path names, from the pane's folder and home; null when it cannot. */
export function absolutePath(value, cwd, home) {
  let p;
  if (value.startsWith("/")) p = value;
  else if (value === "~" || value.startsWith("~/")) {
    if (!home) return null;
    p = home + value.slice(1);
  } else {
    if (!cwd) return null;
    p = `${cwd}/${value}`;
  }
  const out = [];
  for (const part of p.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") out.pop();
    else out.push(part);
  }
  return "/" + out.join("/");
}
