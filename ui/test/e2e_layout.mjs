// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-01 header layout: dot, title and buttons on the first line; the mode badge left of the
// path on the second; the bridge's note on the pane in the footer.
import { check } from "./e2e_harness.mjs";

export async function layoutTools(page) {
  const l = await page.evaluate(() => {
    const box = (id) => document.getElementById(id).getBoundingClientRect();
    const mode = box("mode"), crumbs = box("crumbs"), title = box("title");
    return {
      badgeLeftOfPath: mode.right <= crumbs.left + 1 && Math.abs((mode.top + mode.bottom) / 2 - (crumbs.top + crumbs.bottom) / 2) < 6,
      titleAbove: title.bottom <= crumbs.top + 1,
      badgeNotOnTop: document.getElementById("mode").closest(".top") === null,
      foot: document.getElementById("foot").textContent, note: document.getElementById("note").textContent,
      footAtBottom: box("foot").bottom >= innerHeight - 1,
    };
  });
  check("AC-01 the mode badge sits left of the path, on the line below the title", l.badgeLeftOfPath && l.titleAbove && l.badgeNotOnTop, JSON.stringify(l));
  check("AC-01 the bridge's note shows in the footer, not the header", l.foot === "fake bridge" && !l.note.includes("fake bridge") && l.footAtBottom, JSON.stringify(l));
}
