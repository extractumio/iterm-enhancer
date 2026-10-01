// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Panel colors and font follow the iTerm2 profile of the focused pane. The bridge sends
// raw profile values; everything else (hover, borders, dim text) is derived in CSS.

import type { Theme } from "./api";

let current = "";

export function applyTheme(t?: Theme) {
  const key = JSON.stringify(t ?? null);
  if (key === current) return;
  current = key;
  const root = document.documentElement;
  if (!t?.bg || !t.fg) { root.classList.remove("themed"); return; }
  const set = (k: string, v?: string | null) => { if (v) root.style.setProperty(k, v); };
  set("--t-bg", t.bg);
  set("--t-fg", t.fg);
  set("--t-sel", t.sel ?? t.fg);
  set("--t-selfg", t.selfg ?? t.fg);
  set("--t-cursor", t.cursor ?? t.fg);
  set("--t-link", t.link ?? t.ansi?.[12]);
  (t.ansi ?? []).forEach((c, i) => set(`--t-a${i}`, c));
  set("--t-size", String(t.size ?? 13));
  let ff = document.getElementById("termfont") as HTMLStyleElement | null;
  if (!ff) { ff = document.createElement("style"); ff.id = "termfont"; document.head.append(ff); }
  // the profile gives a PostScript name ("JetBrainsMonoNFM-Regular"); local() resolves it,
  // and the fallback chain keeps the layout if the font is not visible to WebKit
  ff.textContent = t.font ? `@font-face { font-family: TermFont; src: local("${t.font.replace(/["\\]/g, "")}"); }` : "";
  root.style.colorScheme = t.dark ? "dark" : "light";
  root.classList.add("themed");
}
