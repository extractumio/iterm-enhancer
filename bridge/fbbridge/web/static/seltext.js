// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The text of a selection in the terminal, as iTerm2 would copy it. Line ends are drawn by CSS
// (generated content is never copied), so the lines are joined here: a hard end is a newline, a
// line iTerm wrapped continues, and in Wrap prose re-joined on the screen keeps its space.

/** Joins the selected parts of lines: {text, eol, join, cont}, in order. Padding after a hard
 *  end is dropped; in Wrap a re-joined line also drops its indentation. */
export function joinLines(parts, wrap) {
  let out = "";
  parts.forEach((p, i) => {
    let t = p.text.replace(/\ufe0e/g, "");          // the text-style mark render.js adds
    if (p.eol) t = t.replace(/\s+$/, "");
    if (wrap && p.cont && i > 0) t = t.replace(/^\s+/, "");
    out += t;
    if (i < parts.length - 1 && p.eol) out += wrap && p.join ? " " : "\n";
  });
  return out;
}

/** The selection's text when it lies in `term`; null otherwise (the browser's own text). */
export function selectedText(term) {
  const sel = getSelection();
  if (!sel?.rangeCount || sel.isCollapsed) return null;
  const range = sel.getRangeAt(0);
  if (!term.contains(range.commonAncestorContainer)) return null;
  const parts = [];
  for (const ln of term.querySelectorAll(".ln")) {
    if (!range.intersectsNode(ln)) continue;
    const r = document.createRange();
    r.selectNodeContents(ln);
    if (ln.contains(range.startContainer)) r.setStart(range.startContainer, range.startOffset);
    if (ln.contains(range.endContainer)) r.setEnd(range.endContainer, range.endOffset);
    const c = ln.classList;
    parts.push({ text: r.toString(), eol: c.contains("eol"), join: c.contains("join"), cont: c.contains("cont") });
  }
  return parts.length ? joinLines(parts, term.classList.contains("wrap")) : null;
}
