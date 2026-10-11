// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The text of rows that go on into each other, as the page joins them (AC-52): a row iTerm
// wrapped goes on at once, a re-joined hard end stands for a space, and a word the program broke
// at the row's edge ("glue", term.js) goes on with neither a space nor the next row's indentation
// nor the padding after it. The underline (linkmarks.js), a click (termlinks.js) and the image
// buttons (widgets.js) read the same text, so they find the same paths.

/** {text, pieces: [{node, at}]}: the rows' text and where each of its text nodes starts in it;
 *  a text node left out (indentation or padding at a broken word) has no piece. */
export function joinedText(rows) {
  let text = "", glued = false;
  const pieces = [];
  rows.forEach((row, k) => {
    const glue = row.classList.contains("glue");
    const walk = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    for (let t = walk.nextNode(); t; t = walk.nextNode()) {
      const cls = t.parentElement.classList;
      if ((glued && cls.contains("lead")) || (glue && cls.contains("tail"))) continue;
      pieces.push({ node: t, at: text.length });
      text += t.data;
    }
    glued = glue;
    if (k < rows.length - 1 && row.classList.contains("eol") && !glue) text += " ";
  });
  return { text, pieces };
}

/** Where (node, offset) is in joinedText's text, or -1 when that node was left out. */
export function offsetOf(pieces, node, offset) {
  const p = pieces.find((x) => x.node === node);
  return p ? p.at + offset : -1;
}
