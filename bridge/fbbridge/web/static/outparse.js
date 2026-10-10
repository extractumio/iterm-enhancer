// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// What in a terminal's output becomes a widget (AC-56): a file printed by a command (cat, bat…),
// fenced code, a Markdown document, a Mermaid diagram, and image files named in it. Pure: rows
// are {text, eol, styled}, as term.js knows them; widgets.js draws what this finds. A wrong
// widget hides real output and a missed one costs nothing, so the rules ask for clear signs.
import { tokens } from "./links.js";

const MAX_LINES = 2000;      // a longer region stays plain text (rendering it would cost more than it gives)
const GAP = 6;               // the most lines of plain prose between two Markdown signs of one document
const IMAGE = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i;

const FENCE = /^(\s*)(`{3,}|~{3,})\s*([\w#+.-]*)[^`]*$/;
const HEADING = /^#{1,6}\s+(?!-\*-)\S/;          // not "# -*- coding: utf-8 -*-"
const TABLE_SEP = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const LIST = /^\s*(?:[-*+]|\d{1,3}[.)])\s+\S/;
const QUOTE = /^\s*>\s?\S/;
const INLINE = /\*\*[^*\s][^*]*\*\*|(?:^|\s)`[^`\s][^`]*`|\[[^\]]+\]\([^)\s]+\)/;
// lines of a program or a configuration rather than prose: they make a run not Markdown
const CODEISH = new RegExp([
  String.raw`[;{}\\]\s*$`, String.raw`^\s*(?:def|class|import|from|function|const|let|var|return|fi|done|esac|then|export|local)\b`,
  String.raw`^\s*[a-z_][\w.-]*:(?:\s+\S|$)`, String.raw`^\s*[\w.$\[\]]+\s*[-+*/]?=\s`, String.raw`\$\{?\w|\$\(|&&|\|\|`,
].join("|"));
// a shell prompt, by its common shapes: "alex@devbox ~ %", "$ ", "❯ ", "➜ dir", "(venv) …$"
const PROMPT = /^(?:\S+@[\w.-]+[:\s].*?[$#%>](?:\s|$)|[$%❯➜λ»›](?:\s|$)|\(\S+\)\s.*?[$%#❯](?:\s|$))/;
// a command that prints one file: the output after it is that file, up to the next prompt
const PRINT = /(?:^|\s)(?:cat|bat|batcat|head|tail|less|more|nl)\s+(?:-\S+\s+)*([^\s|;&<>'"`]+\.([A-Za-z0-9]+))\s*$/;
// a diagram without a fence starts with a line that is only its keyword ("pie", "timeline" are prose too)
const DIAGRAM = /^(?:(?:graph|flowchart)\s+(?:TD|TB|BT|RL|LR)|sequenceDiagram|classDiagram(?:-v2)?|stateDiagram(?:-v2)?|erDiagram|gitGraph)\s*$/;
const MARKDOWN_EXT = /^(md|markdown|mdown|mkd)$/i, MERMAID_EXT = /^(mmd|mermaid)$/i;

/** Rows joined where iTerm wrapped them (no hard end): {text, from, to, styled} with row
 *  indexes; styled: a row of it has colors of its own. */
export function logicalLines(rows) {
  const out = [];
  let cur = null;
  rows.forEach((r, i) => {
    if (!cur) cur = { text: "", from: i, to: i, styled: false };
    cur.text += r.text;
    cur.to = i;
    cur.styled ||= !!r.styled;
    if (r.eol) { out.push(cur); cur = null; }
  });
  if (cur) out.push(cur);
  return out;
}

/** The regions of `rows` that become widgets, in order and never overlapping:
 *  {kind: "code"|"markdown"|"mermaid", from, to (row indexes, inclusive), lang, text, guessed}.
 *  `end` (exclusive) is the first row that is not output yet (the cursor's, which may hold the
 *  next prompt); a fence still open there is left for later. Output a program colored itself
 *  (bat, glow, delta, a coding agent's own rendering) is never one: it is rendered already. */
export const regions = (rows, end) => scan(rows, end).found;

/** regions(), and `settled`: the first row whose regions may still change as rows are added
 *  after `end` (a fence or a file not finished, a document that may go on); scanning again from
 *  there finds the same regions after it. */
export function scan(rows, end = rows.length) {
  const lines = logicalLines(rows.slice(0, end));
  const next = rows[end]?.text ?? null;                           // the cursor's row
  const found = [];
  let open = lines.length;                                        // the first line still undecided
  let noRun = -1;
  const add = (kind, a, b, lang, text, guessed = false) => {
    if (b - a + 1 > MAX_LINES || !text.trim() || lines.slice(a, b + 1).some((l) => l.styled)) return;
    found.push({ kind, from: lines[a].from, to: lines[b].to, lang, text, guessed, line: a, lastLine: b });
  };
  for (let i = 0; i < lines.length; i++) {
    const printed = printedFile(lines, i, next);
    if (printed === null) open = Math.min(open, i);              // its prompt has not come back yet
    if (printed) {
      if (printed.to >= printed.from) add(printed.kind, printed.from, printed.to, printed.lang, body(lines, printed.from, printed.to));
      i = printed.to;
      continue;
    }
    // a run that is no document has none inside it either (its fences are still looked at)
    const doc = i > noRun ? markdownRun(lines, i) : null;
    if (doc?.open) open = Math.min(open, i);
    if (doc?.ok) { add("markdown", i, doc.to, "markdown", body(lines, i, doc.to)); i = doc.to; continue; }
    if (doc) noRun = doc.to;
    const fence = fenced(lines, i);
    if (fence === null && lines.length - i <= MAX_LINES) { open = Math.min(open, i); break; }   // still printing: it may close
    if (fence) {
      const kind = /^mermaid$/i.test(fence.lang) ? "mermaid" : "code";
      add(kind, i, fence.to, fence.lang, fence.text);
      i = fence.to;
      continue;
    }
    if (DIAGRAM.test(lines[i].text) && !lines[i - 1]?.text.trim()) {
      let j = i;
      while (j + 1 < lines.length && lines[j + 1].text.trim() && !PROMPT.test(lines[j + 1].text)) j++;
      if (j > i) { add("mermaid", i, j, "mermaid", body(lines, i, j), true); i = j; }
    }
  }
  // a region near the end may still grow (a document, a diagram), the last line too (no hard end);
  // but no further back than a region can be long: a scan never grows with the history
  open = Math.max(Math.min(open, Math.max(0, lines.length - GAP - 2)), lines.length - MAX_LINES);
  for (const r of found) if (r.lastLine >= open) open = Math.min(open, r.line);
  const settled = open < lines.length ? lines[open].from : Math.min(end, rows.length);
  return { found: found.map(({ line, lastLine, ...r }) => r), settled };
}

const body = (lines, a, b) => lines.slice(a, b + 1).map((l) => l.text).join("\n");

// A command line that prints one file ("alex@devbox ~ % cat README.md"): the lines after it up
// to the next line that starts with the same prompt (the cursor's row too). A prompt of one or
// two characters ("$ ") is too common in files to end one. Else undefined: the lines are
// looked at one by one, as any output; null when its prompt has not come back yet.
function printedFile(lines, i, next) {
  const m = lines[i].text.match(PRINT);
  if (!m) return undefined;
  const prompt = lines[i].text.slice(0, m.index + (m[0].startsWith(" ") ? 1 : 0)).trimEnd();
  if (prompt.length < 3 || !PROMPT.test(prompt + " ")) return undefined;
  const ext = m[2];
  const kind = MARKDOWN_EXT.test(ext) ? "markdown" : MERMAID_EXT.test(ext) ? "mermaid" : "code";
  const found = (to) => ({ kind, lang: kind === "code" ? m[1].split("/").pop() : kind, from: i + 1, to });
  for (let j = i + 1; j < lines.length; j++) if (lines[j].text.startsWith(prompt)) return found(j - 1);
  return next?.startsWith(prompt) ? found(lines.length - 1) : lines.length - i <= MAX_LINES ? null : undefined;
}

// A fence opened at line i: {lang, text, to} when closed, null when not closed yet.
function fenced(lines, i) {
  const open = lines[i].text.match(FENCE);
  if (!open) return undefined;
  const [, indent, mark] = open;
  const close = new RegExp(`^\\s*${mark[0] === "`" ? "`" : "~"}{${mark.length},}\\s*$`);
  for (let j = i + 1; j < lines.length; j++) {
    if (close.test(lines[j].text)) {
      const text = lines.slice(i + 1, j).map((l) => (l.text.startsWith(indent) ? l.text.slice(indent.length) : l.text.trimStart())).join("\n");
      return { lang: open[3], text, to: j };
    }
  }
  return null;
}

// What a line says about Markdown: a strong sign, a weak one, or none.
function sign(lines, i) {
  const t = lines[i].text;
  if (HEADING.test(t)) {
    // a heading stands after a blank line; "#" lines in a run of code are its comments
    const prev = lines[i - 1]?.text ?? "";
    return prev.trim() || HEADING.test(lines[i + 1]?.text ?? "") ? "" : "heading";
  }
  if (TABLE_SEP.test(t)) return "table";
  if (FENCE.test(t)) return "fence";
  if (LIST.test(t)) return "list";
  if (QUOTE.test(t)) return "quote";
  if (INLINE.test(t)) return "inline";
  return "";
}

// A run of Markdown starting at line i: from a sign, on over gaps of at most GAP lines to the
// last sign and the paragraph after it, never over a prompt; a fence inside it is part of it.
// It is a document (ok) when it has a heading, a table or a fence, two kinds of signs, three
// lines with a sign, and few lines of code. null: no sign at i; open: it reached the end and
// may go on. {ok, to, open}.
function markdownRun(lines, i) {
  if (!sign(lines, i)) return null;
  const kinds = new Set();
  let last = i, signs = 0, codeish = 0, filled = 0, j = i, open = false;
  for (; j < lines.length && j - last <= GAP && j - i < MAX_LINES; j++) {
    const t = lines[j].text;
    if (PROMPT.test(t)) break;
    const s = sign(lines, j);
    if (s === "fence") {
      const f = fenced(lines, j);
      if (!f) { open = true; break; }                             // not closed (yet): the run ends before it
      kinds.add(s); signs++; filled += f.to - j + 1; last = j = f.to;
      continue;
    }
    if (t.trim()) { filled++; if (CODEISH.test(t)) codeish++; }
    if (s) { kinds.add(s); signs++; last = j; }
  }
  open ||= j >= lines.length && j - i < MAX_LINES;
  const strong = kinds.has("heading") || kinds.has("table") || kinds.has("fence");
  if (!strong || kinds.size < 2 || signs < 3 || codeish > filled * 0.3) return { ok: false, to: last, open };
  let to = last;                                                  // the paragraph after the last sign
  const prose = (k) => k < lines.length && lines[k].text.trim() && !PROMPT.test(lines[k].text);
  if (!prose(to + 1) && prose(to + 2)) to++;                      // after a blank line
  while (prose(to + 1)) to++;
  return { ok: true, to, open };
}

/** Image files named in a line's text: [{value, start, end}], as links.js finds paths. */
export function imagePaths(text) {
  return tokens(text).filter((t) => t.kind === "path" && IMAGE.test(t.value)).map(({ value, start, end }) => ({ value, start, end }));
}
