// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Markdown → HTML with typography. Raw HTML is disabled (a README must not run script),
// relative images are served through /api/raw, links are handled by the viewer.

import MarkdownIt from "markdown-it";
import taskLists from "markdown-it-task-lists";
import footnote from "markdown-it-footnote";
import { dirname, rawUrl, resolvePath } from "./api";
import { highlightInto } from "./highlight";

const md = new MarkdownIt({ html: false, linkify: true, typographer: true, breaks: false })
  .use(taskLists, { enabled: false, label: true })
  .use(footnote);

// Only these link schemes survive; everything else (javascript:, file:, data: for links) is dropped.
md.validateLink = (url) => /^(https?:|mailto:|#|\.{0,2}\/|[^:]*$)/i.test(url.trim());

const isExternal = (u: string) => /^[a-z][a-z0-9+.-]*:/i.test(u);
const safeDecode = (u: string) => { try { return decodeURI(u); } catch { return u; } };

md.renderer.rules.image = (tokens, idx, _opts, env) => {
  const t = tokens[idx];
  const src = String(t.attrGet("src") ?? "");
  const url = isExternal(src) || src.startsWith("data:") ? "" : rawUrl(resolvePath(String(env?.dir ?? "/"), safeDecode(src.split("#")[0])));
  const alt = md.utils.escapeHtml(t.content);
  // remote images are not loaded (CSP img-src 'self'); show the alt text as a link instead
  return url
    ? `<img src="${md.utils.escapeHtml(url)}" alt="${alt}" loading="lazy">`
    : `<a href="${md.utils.escapeHtml(src)}" class="remote-img">🖼 ${alt || src}</a>`;
};

// heading anchors, so "#section" links work inside the document
md.renderer.rules.heading_open = (tokens, idx, opts, _env, self) => {
  const inline = tokens[idx + 1];
  const slug = inline?.content.toLowerCase().trim().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s/g, "-"); // GitHub-compatible slugs
  if (slug) tokens[idx].attrSet("id", slug);
  return self.renderToken(tokens, idx, opts);
};

export function isMarkdown(path: string) {
  return /\.(md|markdown|mdx|mdown|mkd)$/i.test(path);
}

/** Markdown → HTML string; relative links and images resolve against the file's folder. */
export function mdToHtml(text: string, path: string): string {
  return md.render(text, { dir: dirname(path) });
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
