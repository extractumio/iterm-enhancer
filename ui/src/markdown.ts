// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Markdown → HTML with typography (markdown-core.ts), relative images served through
// /api/raw; links are handled by the viewer.

import { dirname, rawUrl } from "./api";
import { highlightInto } from "./highlight";
import { renderMd } from "./markdown-core";

export function isMarkdown(path: string) {
  return /\.(md|markdown|mdx|mdown|mkd)$/i.test(path);
}

/** Markdown → HTML string; relative links and images resolve against the file's folder. */
export function mdToHtml(text: string, path: string): string {
  return renderMd(text, { dir: dirname(path), imageUrl: rawUrl });
}

/** Render `text` (the content of the file at `path`) into `el`, then highlight fenced code. */
export function renderMarkdown(el: HTMLElement, text: string, path: string) {
  el.innerHTML = mdToHtml(text, path);
  for (const code of el.querySelectorAll<HTMLElement>("pre > code[class*='language-']")) {
    const lang = [...code.classList].find((c) => c.startsWith("language-"))!.slice(9);
    code.parentElement!.dataset.lang = lang;
    void highlightInto(code, code.textContent ?? "", lang);
  }
}
