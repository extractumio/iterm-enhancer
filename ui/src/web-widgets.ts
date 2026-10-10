// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The renderers of the web app's output widgets (AC-56), served by fbd as /renderers.js and loaded
// by the web page through its /fb proxy on first need: the panel's own Markdown rules (raw HTML
// off, only safe link schemes) and highlighter, and Mermaid, loaded only for a diagram.

import { renderMd } from "./markdown-core";
import { highlightInto } from "./highlight";

/** Markdown → HTML, as the panel renders a document; images show as their alt text (the page
 *  loads no image a terminal's text names, local or remote). Links are the page's to handle. */
export const markdownHtml = (text: string): string => renderMd(text, { dir: "/", imageUrl: () => "" });

/** Highlights `code` into `into` (tok-* spans); false when the language is not known. `lang`
 *  is a fence's word ("py") or a file's name ("tool.py"). */
export const highlight = (into: HTMLElement, code: string, lang: string) => highlightInto(into, code, lang);

let mermaidLib: Promise<typeof import("mermaid").default> | null = null;
let shownTheme = "";
let seq = 0;

/** A Mermaid diagram's SVG markup. Strict: no click handlers, no HTML labels (the SVG is shown
 *  as an image, where HTML inside it would not draw). Throws Mermaid's message on bad source. */
export async function mermaidSvg(source: string, dark: boolean): Promise<string> {
  mermaidLib ??= import("mermaid").then((m) => m.default);
  const mermaid = await mermaidLib;
  const theme = dark ? "dark" : "default";
  if (theme !== shownTheme) {
    // Mermaid's dark pie slices are near black on a dark terminal: readable ones instead
    const pies = ["#5b8def", "#d0679d", "#3fb5a3", "#e0a240", "#9b7be0", "#e06c5b", "#6bbf59", "#56b3d9"];
    const themeVariables = dark ? Object.fromEntries(pies.map((c, i) => [`pie${i + 1}`, c])) : {};
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme, themeVariables, htmlLabels: false,
      flowchart: { htmlLabels: false }, fontFamily: "-apple-system, BlinkMacSystemFont, Helvetica, sans-serif" });
    shownTheme = theme;
  }
  const { svg } = await mermaid.render(`wg-mermaid-${++seq}`, source);
  return svg;
}
