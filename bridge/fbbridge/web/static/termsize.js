// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Measures of the terminal view (a TermView): its inner size, the terminal font's cell width,
// the text size chosen, and what fits; and from them how Wrap shows a full-screen program.

/** The text area's inner size in px. */
export function inner(view) {
  const cs = getComputedStyle(view.term);
  return { cs, w: view.term.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
    h: view.term.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) };
}

/** One cell's advance per px of font size, for the terminal font now loaded (view.ratio caches it). */
export function charRatio(view) {
  if (view.ratio) return view.ratio;
  const probe = document.createElement("span");
  probe.textContent = "0".repeat(100);
  probe.style.cssText = "position:absolute;visibility:hidden;white-space:pre;font-size:100px";
  view.term.append(probe);
  view.ratio = probe.getBoundingClientRect().width / 100 / 100;
  probe.remove();
  return view.ratio;
}

/** The text size chosen (the page's --size): Grid's, and Fit's at most. */
export const chosenSize = (view) => parseFloat(getComputedStyle(view.term).getPropertyValue("--size"));
/** True when iTerm's rows are wider than the visible area at the chosen size (in Grid). */
export const tooWide = (view) => view.cols * charRatio(view) * chosenSize(view) > inner(view).w;
/** Fit's text size: iTerm's columns in the view's width, at most a little over the chosen size. */
export const fitPx = (view) => Math.min(Math.floor(((inner(view).w - 1) / view.cols / charRatio(view)) * 10) / 10, chosenSize(view) + 4);

/** A full-screen program drawing a frame wider than the view: on the alternate screen, at least
 *  half of the rows with text on them reach past the columns that fit at the chosen size (mc,
 *  htop, a coding agent with its side panel open: borders to the pane's edge; re-flowed, the
 *  frame breaks). Rows that end at their text re-flow (a coding agent's full-screen mode, vim,
 *  tmux); the blanks a program pads its rows with, styled or not, are the padding Wrap hides
 *  (render.js markEdge). */
export function framed(view) {
  if (!view.alt || !view.cols || !tooWide(view)) return false;
  const fit = Math.floor(inner(view).w / (charRatio(view) * chosenSize(view)));
  let drawn = 0, wide = 0;
  for (const r of view.screen) {
    if (!r || !r.txt.trim()) continue;
    drawn++;
    if (r.txt.length > fit) wide++;
  }
  return wide * 2 >= drawn && drawn > 0;
}

/** How Wrap shows a frame: as Fit while that keeps the text at least `min` px, else as Grid (the
 *  chosen size, the frame's left part in sight, the rest by scrolling sideways); null for no frame. */
export const frameMode = (view, min) => (framed(view) ? (fitPx(view) >= min ? "fit" : "grid") : null);

/** Columns and rows that fit the visible area at the current font size. */
export function gridFit(view) {
  const { cs, w, h } = inner(view);
  return { cols: Math.floor(w / (charRatio(view) * parseFloat(cs.fontSize))), rows: Math.floor(h / parseFloat(cs.lineHeight)) };
}
