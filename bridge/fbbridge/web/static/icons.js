// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The web app's icons: Lucide (https://lucide.dev), drawn on its 24-unit grid with its 2-unit
// stroke. The path data below is from lucide-static 1.52.0, under this license:
//
//   ISC License. Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part
//   of Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.
//   Permission to use, copy, modify, and/or distribute this software for any purpose with or
//   without fee is hereby granted, provided that the above copyright notice and this permission
//   notice appear in all copies. THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL
//   WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
//   AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR
//   CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
//   WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
//   CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

const SVG = "http://www.w3.org/2000/svg";

// name -> shapes: a string is a path, [cx, cy, r] a circle
const ICONS = {
  hourglass: ["M5 22h14", "M5 2h14", "M17 22v-4.172a2 2 0 0 0-.586-1.414L12 12l-4.414 4.414A2 2 0 0 0 7 17.828V22",
    "M7 2v4.172a2 2 0 0 0 .586 1.414L12 12l4.414-4.414A2 2 0 0 0 17 6.172V2"],
  "circle-check": ["M21.801 10A10 10 0 1 1 17 3.335", "m9 11 3 3L22 4"],
  "circle-help": [[12, 12, 10], "M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3", "M12 17h.01"],
  "triangle-alert": ["m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3", "M12 9v4", "M12 17h.01"],
  plus: ["M5 12h14", "M12 5v14"],
  "grip-vertical": [[9, 12, 1], [9, 5, 1], [9, 19, 1], [15, 12, 1], [15, 5, 1], [15, 19, 1]],
  pencil: ["M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z", "m15 5 4 4"],
  search: ["m21 21-4.34-4.34", [11, 11, 8]],
  "chevron-down": ["m6 9 6 6 6-6"],
};

/** An inline Lucide icon at `size` px, drawn in the text color. */
export function icon(name, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "lu");
  svg.style.setProperty("--s", `${size}px`);         // the page's svg rule sizes by it
  svg.setAttribute("aria-hidden", "true");
  for (const s of ICONS[name] ?? []) {
    let el;
    if (typeof s === "string") { el = document.createElementNS(SVG, "path"); el.setAttribute("d", s); }
    else if (Array.isArray(s)) {
      el = document.createElementNS(SVG, "circle");
      [["cx", s[0]], ["cy", s[1]], ["r", s[2]]].forEach(([k, v]) => el.setAttribute(k, String(v)));
    }
    svg.append(el);
  }
  return svg;
}

// A profile's dot: one color per iTerm2 profile name, the same in every browser. Hues that
// statuses use (amber, orange, red, green) are left out so a dot never reads as a state.
const DOTS = ["#7aa2f7", "#bb9af7", "#2dd4bf", "#f472b6", "#7dcfff", "#a3a8ff"];
const KNOWN = { Default: "#7aa2f7", tmux: "#bb9af7" };

export function profileColor(name) {
  if (!name) return "#8b919c";
  if (KNOWN[name]) return KNOWN[name];
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return DOTS[h % DOTS.length];
}
