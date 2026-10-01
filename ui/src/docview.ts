// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Rendered documents (Markdown and HTML) and the links inside them. A link never navigates
// the panel: anchors scroll, web links go to the default browser, files open as tabs
// (`guide.md#install` opens guide.md and scrolls to "install").

import { api, apiOrToast, basename, dirname, rawUrl, resolvePath, toast, type FileView } from "./api";
import { isMarkdown, renderMarkdown } from "./markdown";

export const isHtml = (path: string) => /\.x?html?$/i.test(path);
export const isRenderable = (path: string) => isMarkdown(path) || isHtml(path);

export interface LinkHost {
  open(path: string, anchor?: string): void;
  prefetched(path: string, file: FileView): void; // the link check already read the file
}

/** Scroll `scope` to the element with id (or name) `anchor`. */
export function scrollToAnchor(scope: Document | HTMLElement, anchor: string, smooth = true) {
  const id = CSS.escape(anchor);
  scope.querySelector(`[id="${id}"], [name="${id}"]`)?.scrollIntoView({ behavior: smooth ? "smooth" : "instant", block: "start" });
}

/** Follow `href` found in the document at `from`; `scope` is where in-page anchors live. */
export async function followLink(href: string, from: string, scope: Document | HTMLElement, host: LinkHost) {
  if (href.startsWith("#")) return scrollToAnchor(scope, decodeURIComponent(href.slice(1)));
  if (/^(https?:|mailto:)/i.test(href)) return void apiOrToast("POST", "/api/os/open", { body: { url: href } });
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !href.startsWith("file:")) return toast(`Not opened: ${href.split(":")[0]} links`);
  const [file, anchor] = href.replace(/^file:\/\//, "").split("#");
  let target: string;
  try { target = resolvePath(dirname(from), decodeURIComponent(file)); } catch { return toast(`Bad link: ${href}`); }
  try {
    host.prefetched(target, await api<FileView>("GET", "/api/file", { query: { path: target } }));
    host.open(target, anchor ? decodeURIComponent(anchor) : undefined);
  } catch (e: any) {
    toast(`${basename(target)}: ${e.message}`);
  }
}

/** Render `text` (the document at `path`) into `box`, wire its links, scroll to `anchor`. */
export function renderDoc(box: HTMLElement, text: string, path: string, host: LinkHost, anchor?: string) {
  const onClick = (scope: Document | HTMLElement) => (e: MouseEvent) => {
    const a = (e.target as Element).closest?.("a[href]");
    if (!a) return;
    e.preventDefault();
    void followLink(a.getAttribute("href")!, path, scope, host);
  };
  if (isMarkdown(path)) {
    box.innerHTML = '<article class="md-body"></article>';
    const article = box.firstElementChild as HTMLElement;
    renderMarkdown(article, text, path);
    article.addEventListener("click", onClick(article));
    if (anchor) requestAnimationFrame(() => scrollToAnchor(article, anchor, false));
    return;
  }
  // HTML: a sandboxed frame without scripts; same origin only so we can route its links
  const frame = document.createElement("iframe");
  frame.className = "html-doc";
  frame.setAttribute("sandbox", "allow-same-origin");
  frame.srcdoc = prepareHtml(text, path);
  frame.addEventListener("load", () => {
    const doc = frame.contentDocument;
    if (!doc) return;
    doc.addEventListener("click", onClick(doc));
    if (anchor) scrollToAnchor(doc, anchor, false); // opening at a section: jump, do not animate
  });
  box.replaceChildren(frame);
}

/** Make an HTML file safe and self-contained: no scripts or frames, local resources through
 *  /api/raw, and the panel's colors as a fallback for unstyled pages. */
function prepareHtml(text: string, path: string): string {
  const doc = new DOMParser().parseFromString(text, "text/html");
  doc.querySelectorAll("script, iframe, frame, object, embed, base, meta").forEach((el) => el.remove());
  for (const el of doc.querySelectorAll("*")) {
    for (const attr of [...el.attributes]) if (/^on/i.test(attr.name)) el.removeAttribute(attr.name);
  }
  const local = (url: string | null) => !!url && !/^([a-z][a-z0-9+.-]*:|#|\/\/)/i.test(url);
  const raw = (url: string) => rawUrl(resolvePath(dirname(path), decodeURIComponent(url.split(/[?#]/)[0])));
  for (const el of doc.querySelectorAll<HTMLElement>("img[src], source[src], video[src], audio[src], input[src]")) {
    const src = el.getAttribute("src");
    if (local(src)) el.setAttribute("src", raw(src!));
  }
  for (const el of doc.querySelectorAll('link[rel~="stylesheet"][href]')) {
    const href = el.getAttribute("href");
    if (local(href)) el.setAttribute("href", raw(href!)); else el.remove();
  }
  const cs = getComputedStyle(document.documentElement);
  const base = doc.createElement("style");
  base.textContent = `html{color-scheme:${cs.colorScheme || "normal"}}body{margin:0;padding:20px 24px;` +
    `font:15px/1.6 -apple-system,BlinkMacSystemFont,sans-serif;color:${cs.getPropertyValue("--fg")};` +
    `background:${cs.getPropertyValue("--bg")}}a{color:${cs.getPropertyValue("--link")}}img{max-width:100%}`;
  doc.head.prepend(base); // first, so the page's own styles win
  // the document's own policy: nothing may load from outside fbd, whatever the parent passes down
  const csp = doc.createElement("meta");
  csp.httpEquiv = "Content-Security-Policy";
  csp.content = "default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self' data:";
  const referrer = Object.assign(doc.createElement("meta"), { name: "referrer", content: "no-referrer" });
  doc.head.prepend(csp, referrer);
  return "<!doctype html>" + doc.documentElement.outerHTML;
}
