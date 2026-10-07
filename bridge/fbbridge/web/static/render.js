// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Draws one iTerm2 line ({r: [[text-or-cells, fg, bg, flags]...], e: hardEol}) as a DOM element.
// Plain DOM text keeps the browser's own selection, copy, scrolling and wrapping, also on iOS.

const BOLD = 1, FAINT = 2, ITALIC = 4, UNDERLINE = 8, STRIKE = 16, INVERSE = 32, INVISIBLE = 64,
  CURSOR = 256;
const RULE = /^[─━═╌╍┄┅┈┉\-_=~]{8,}$/;     // a horizontal separator line

export function palette(theme) {
  const p = [...theme.ansi];
  const lv = [0, 95, 135, 175, 215, 255];
  for (let r = 0; r < 6; r++) for (let g = 0; g < 6; g++) for (let b = 0; b < 6; b++)
    p.push(`rgb(${lv[r]},${lv[g]},${lv[b]})`);
  for (let i = 0; i < 24; i++) { const v = 8 + i * 10; p.push(`rgb(${v},${v},${v})`); }
  return p;
}

// iOS draws symbols such as ✳ ❯ ☁ as colored emoji; a terminal draws them as text. U+FE0E asks
// for the text form unless the program asked for emoji (U+FE0F) itself.
export function textForm(s) {
  return s.replace(/([\u2190-\u2bff])(?![\ufe0e\ufe0f])/g, "$1\ufe0e");
}

function resolve(c, theme, pal, fallback) {
  if (c === null || c === undefined) return fallback;
  if (c === "R") return fallback === theme.fg ? theme.bg : theme.fg;
  if (typeof c === "number") return pal[c] ?? fallback;
  return c;
}

// Symbols, box drawing, emoji and CJK often come from a fallback font whose advance differs
// from the terminal font, which shifts the rest of the line. iTerm2 draws every cell on a fixed
// grid, so such a cell goes into a box exactly 1ch (or 2ch for a wide character) wide.
function fillCells(s, cells) {
  let plain = "";
  for (let i = 0; i < cells.length; i++) {
    const c = cells[i];
    if (c === "") continue;                       // right half of a wide character
    if (c.length === 1 && c < "\u2000") { plain += c; continue; }
    if (plain) { s.append(plain); plain = ""; }
    const box = document.createElement("span");
    box.className = cells[i + 1] === "" ? "c2" : "c1";
    box.textContent = textForm(c);
    s.append(box);
  }
  if (plain) s.append(plain);
}

function span([text, fg, bg, flags], theme, pal) {
  const s = document.createElement("span");
  // terminal content is untrusted: text nodes only, never innerHTML
  if (Array.isArray(text)) fillCells(s, text); else s.textContent = text;
  // iTerm2 reports an inverse cell as "reversed default" colors plus the inverse flag; swap once.
  if (flags & INVERSE) { if (fg === "R") fg = null; if (bg === "R") bg = null; }
  let f = resolve(fg, theme, pal, theme.fg);
  let b = resolve(bg, theme, pal, null);
  if (flags & BOLD && fg === null && theme.useBold && theme.bold) f = theme.bold;
  if (flags & INVERSE) [f, b] = [b ?? theme.bg, f];
  if (flags & CURSOR) { s.className = "cur"; f = theme.cursorText; b = theme.cursor; }
  if (f !== theme.fg) s.style.color = f;
  if (b) s.style.background = b;
  if (flags & BOLD) s.style.fontWeight = "bold";
  if (flags & ITALIC) s.style.fontStyle = "italic";
  if (flags & FAINT) s.style.opacity = "0.55";
  if (flags & INVISIBLE) s.style.color = "transparent";
  const deco = [flags & UNDERLINE ? "underline" : "", flags & STRIKE ? "line-through" : ""].join(" ").trim();
  if (deco) s.style.textDecoration = deco;
  return s;
}

// Wraps the leading or trailing whitespace of el in <span class=cls>, never touching the cursor.
// In Wrap mode CSS hides the trailing part (padding to iTerm's width that would stick out of a
// phone screen) and, on a re-joined line, the leading indentation.
function markEdge(el, fromEnd, cls) {
  const nodes = [];
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) nodes.push(w.currentNode);
  if (fromEnd) nodes.reverse();
  const re = fromEnd ? /\s+$/ : /^\s+/;
  for (const node of nodes) {
    if (node.parentElement.closest(".cur")) return;
    const m = node.data.match(re);
    if (!m) return;
    const whole = m[0].length === node.data.length;
    let part = node;
    if (!whole) part = fromEnd ? node.splitText(node.data.length - m[0].length) : (node.splitText(m[0].length), node);
    const wrap = document.createElement("span");
    wrap.className = cls;
    part.replaceWith(wrap);
    wrap.append(part);
    if (!whole) return;
  }
}

// A record per line: the element plus its plain text, for re-joining hard-wrapped prose.
export function lineRec(data, theme, pal) {
  const el = document.createElement("div");
  el.className = data.e ? "ln eol" : "ln";
  for (const run of data.r) el.append(span(run, theme, pal));
  const txt = el.textContent.replace(/[\ufe0e\ufe0f]/g, "").replace(/\s+$/, "");
  markEdge(el, false, "lead");
  if (data.e) markEdge(el, true, "tail");
  if (RULE.test(txt.trim())) el.classList.add("rule");
  return { data, el, txt };
}
