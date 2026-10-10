// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Faint dotted underlines under the web addresses and file paths of the terminal (AC-52), so it
// shows what opens on a click (with ⌘ on a Mac, Ctrl elsewhere) or a tap. They are drawn with
// the CSS Custom Highlight API: the lines' DOM stays as it is (iOS's selection, copying and the
// cursor's maths depend on it). Only the paragraphs within a screen of the visible ones are
// looked at, after a redraw and when scrolling settles; a browser without the API shows none.
import { tokens } from "./links.js";

const NEAR = 1;                 // screens above and below the visible one
// A row goes on in the next one when the program wrapped it or the page re-joined it (a
// re-joined hard end stands for a space), as for a click (termlinks.js).
const continues = (el) => !el.classList.contains("eol") || el.classList.contains("join");

/** The rows within a screen of the visible ones, top to bottom: the boxes' children are blocks
 *  and loose rows. Rows hidden by a widget (AC-56) and an agent's status lines are left out:
 *  what is not drawn has no position, which the search needs. */
export function nearRows(term, boxes) {
  if (term.hidden) return [];
  const kids = boxes.flatMap((b) => [...b.children]).filter((k) => !k.classList.contains("foot") && !k.classList.contains("wg-hid"));
  const view = term.getBoundingClientRect();
  const lo = view.top - NEAR * view.height, hi = view.bottom + NEAR * view.height;
  let a = 0, b = kids.length;
  while (a < b) { const m = (a + b) >> 1; if (kids[m].getBoundingClientRect().bottom < lo) a = m + 1; else b = m; }
  const rows = [];
  for (let i = a; i < kids.length && kids[i].getBoundingClientRect().top <= hi; i++) {
    if (kids[i].classList.contains("ln")) rows.push(kids[i]);
    else rows.push(...kids[i].querySelectorAll(".ln:not(.wg-hid)"));
  }
  return rows;
}

export function linkMarks(term, boxes) {
  if (!globalThis.CSS?.highlights || typeof Highlight === "undefined") return () => {};
  let timer = 0;
  const later = (ms = 120) => { clearTimeout(timer); timer = setTimeout(mark, ms); };
  term.addEventListener("scroll", () => later(), { passive: true });
  addEventListener("resize", () => later());

  // One paragraph's text, and where each of its text nodes starts in it.
  function ranges(rows) {
    let text = "";
    const pieces = [];
    for (const row of rows) {
      const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
      for (let t = walk.nextNode(); t; t = walk.nextNode()) { pieces.push({ node: t, at: text.length }); text += t.data; }
      if (row.classList.contains("join")) text += " ";
    }
    // the text node an offset falls in: the last one starting at or before it (an end: before it)
    const point = (offset, end) => {
      let a = 0, b = pieces.length;
      while (a < b) { const m = (a + b) >> 1; if (end ? pieces[m].at < offset : pieces[m].at <= offset) a = m + 1; else b = m; }
      const p = pieces[a - 1];
      return p ? { node: p.node, offset: Math.min(offset - p.at, p.node.length) } : null;
    };
    const out = [];
    for (const tok of tokens(text)) {
      const a = point(tok.start, false), b = point(tok.end, true);
      // static: they cost nothing while the page changes, and survive a row moving into a block
      if (a && b) out.push(new StaticRange({ startContainer: a.node, startOffset: a.offset, endContainer: b.node, endOffset: b.offset }));
    }
    return out;
  }

  function mark() {
    clearTimeout(timer);
    const all = [];
    let para = [];
    for (const row of nearRows(term, boxes)) {
      para.push(row);
      if (!continues(row)) { all.push(...ranges(para)); para = []; }
    }
    if (para.length) all.push(...ranges(para));
    CSS.highlights.set("lnk", new Highlight(...all));
  }
  return () => later(60);
}
