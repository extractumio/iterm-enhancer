// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The one Markdown configuration: raw HTML is disabled (a README must not run script), only
// safe link schemes survive, remote images are not loaded. No token here: the web app's output
// widgets (AC-56) render with it outside the panel, and say how images are shown.

import MarkdownIt from "markdown-it";
import taskLists from "markdown-it-task-lists";
import footnote from "markdown-it-footnote";
import { decodePath, hasScheme, resolvePath } from "./paths";

/** env: the folder relative links and images resolve against, and the URL of a local image
 *  ("" when it is not loaded: its alt text shows instead). */
export type MdEnv = { dir: string; imageUrl: (path: string) => string };

const md = new MarkdownIt({ html: false, linkify: true, typographer: true, breaks: false })
  .use(taskLists, { enabled: false, label: true })
  .use(footnote);

// Only these link schemes survive; everything else (javascript:, file:, data: for links) is dropped.
md.validateLink = (url) => /^(https?:|mailto:|#|\.{0,2}\/|[^:]*$)/i.test(url.trim());

md.renderer.rules.image = (tokens, idx, _opts, e) => {
  const env = e as MdEnv;
  const t = tokens[idx];
  const src = String(t.attrGet("src") ?? "");
  const url = hasScheme(src) || src.startsWith("data:") ? "" : env.imageUrl(resolvePath(env.dir, decodePath(src.split(/[?#]/)[0])));
  const alt = md.utils.escapeHtml(t.content);
  // remote images are not loaded (CSP img-src 'self'); show the alt text as a link instead
  return url
    ? `<img src="${md.utils.escapeHtml(url)}" alt="${alt}" loading="lazy">`
    : `<a href="${md.utils.escapeHtml(src)}" class="remote-img">🖼 ${alt || md.utils.escapeHtml(src)}</a>`;
};

// heading anchors, so "#section" links work inside the document
md.renderer.rules.heading_open = (tokens, idx, opts, _env, self) => {
  const inline = tokens[idx + 1];
  const slug = inline?.content.toLowerCase().trim().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s/g, "-"); // GitHub-compatible slugs
  if (slug) tokens[idx].attrSet("id", slug);
  return self.renderToken(tokens, idx, opts);
};

export const renderMd = (text: string, env: MdEnv): string => md.render(text, env);
