// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Inline SVG icons, tinted through CSS variables so they follow the terminal theme.

export const chevron =
  '<svg width="8" height="8" viewBox="0 0 8 8"><path d="M2 1l4 3-4 3" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';

export const folderIcon = (open: boolean) =>
  open
    ? '<svg class="ico" viewBox="0 0 16 16"><path d="M1.5 3.5h4.2l1.5 1.6h7.3v1.4H4.2L1.5 13.5z" fill="var(--folder)" opacity=".75"/><path d="M1.5 13.5l2.7-7h11.3l-2.7 7z" fill="var(--folder)"/></svg>'
    : '<svg class="ico" viewBox="0 0 16 16"><path d="M1.5 3.5h4.2l1.5 1.6h7.3v8.4h-13z" fill="var(--folder)" opacity=".9"/></svg>';

const KIND: [RegExp, string][] = [
  [/\.(js|mjs|cjs|ts|tsx|jsx|py|rs|go|c|h|cc|cpp|hpp|java|kt|swift|rb|php|lua|sh|bash|zsh|pl|scala|cs|dart|zig|ex|exs|erl|hs|ml|clj|r|sql)$/i, "var(--k-code)"],
  [/(\.(json|ya?ml|toml|ini|conf|cfg|env|lock|xml|plist|properties)|^(Makefile|Dockerfile|\.gitignore|\.editorconfig))$/i, "var(--k-conf)"],
  [/\.(md|markdown|mdx|txt|rst|log|csv|tsv|adoc)$/i, "var(--k-doc)"],
  [/\.(png|jpe?g|gif|svg|webp|ico|heic|bmp|tiff?|avif|pdf)$/i, "var(--k-img)"],
  [/\.(zip|gz|tgz|bz2|xz|7z|tar|dmg|iso|rar|zst)$/i, "var(--k-arch)"],
  [/\.(html?|css|scss|less|vue|svelte)$/i, "var(--k-web)"],
];

const fileColor = (name: string) => KIND.find(([re]) => re.test(name))?.[1] ?? "var(--fg-faint)";

export const fileIcon = (name: string) => {
  const c = fileColor(name);
  return `<svg class="ico" viewBox="0 0 16 16"><path d="M3.5 1.5h6l3 3v10h-9z" fill="none" stroke="${c}" stroke-width="1.2"/><path d="M9.5 1.5v3h3" fill="none" stroke="${c}" stroke-width="1.2"/></svg>`;
};
