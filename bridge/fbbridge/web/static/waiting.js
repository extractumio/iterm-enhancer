// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Reminders of the other sessions whose coding agent waits for an answer (AC-52): a card each
// over the terminal, at its side on a computer (three at most) and one slim line under the
// header on a phone, so a question asked in another session is not missed. A card goes when the
// user closes it, opens that session (from the card or the list), or the session stops waiting;
// it comes back the next time that session asks. "Waiting" is read off the screen, and the
// bridge sends a layout only when something changed, so a session counts as waiting once it has
// waited STEADY ms with no layout saying otherwise, and stops STEADY ms after one does.
import { icon } from "./icons.js";
import { textForm } from "./render.js";

const PHONE = matchMedia("(max-width: 860px)");
const STEADY = 2500;

export function waitingCards(box, onOpen) {
  const asking = new Map();        // id -> {since, title}: waiting, as the last layout said
  const leaving = new Map();       // id -> {since, title}: stopped waiting after it counted
  const seen = new Set();          // shown or closed while it waits
  const cards = new Map();         // id -> card element
  let shown = null;

  function card(id) {
    const c = document.createElement("div");
    c.className = "ask";
    const go = document.createElement("button");
    go.type = "button";
    go.className = "go";
    const mark = document.createElement("span");
    mark.className = "state waiting";
    mark.append(icon("circle-help", 14));
    const text = document.createElement("span");
    text.className = "what";
    const more = document.createElement("span");
    more.className = "more";
    go.append(mark, text, more);
    go.addEventListener("click", () => { seen.add(id); onOpen(id); draw(); });
    const x = document.createElement("button");
    x.type = "button";
    x.className = "x";
    x.setAttribute("aria-label", "Close this reminder");
    x.append(icon("x", 14));
    x.addEventListener("click", () => { seen.add(id); draw(); });
    c.append(go, x);
    return c;
  }

  const put = (el, prop, value) => { if (el[prop] !== value) el[prop] = value; };   // a live region re-reads every write

  function draw() {
    const now = performance.now(), titles = new Map();
    for (const [id, w] of asking) if (now - w.since >= STEADY) titles.set(id, w.title);
    for (const [id, w] of leaving) if (now - w.since < STEADY) titles.set(id, w.title); else leaving.delete(id);
    for (const id of seen) if (!asking.has(id) && !leaving.has(id)) seen.delete(id);   // asks again: remind again
    if (asking.has(shown) || leaving.has(shown)) seen.add(shown);
    const due = [...titles.keys()].filter((id) => !seen.has(id));
    for (const [id, c] of cards) if (!due.includes(id)) { c.remove(); cards.delete(id); }
    const room = PHONE.matches ? 1 : 3;
    due.forEach((id, i) => {
      if (!cards.has(id)) { cards.set(id, card(id)); box.append(cards.get(id)); }
      const c = cards.get(id), title = textForm(titles.get(id) || "A session");
      put(c.querySelector(".what"), "textContent", `${title} waits for your answer`);
      put(c.querySelector(".go"), "title", `Open ${title}`);
      const more = i === room - 1 ? due.length - room : 0;       // the last card says how many more wait
      put(c.querySelector(".more"), "textContent", more > 0 ? `+${more} more` : "");
      put(c, "hidden", i >= room);
    });
    put(box, "hidden", !cards.size);
  }
  PHONE.addEventListener("change", draw);

  return {
    /** A layout from the bridge: who waits now. */
    layout(groups) {
      const now = performance.now(), items = groups.flatMap((g) => g.items), ids = new Set(items.map((it) => it.id));
      for (const it of items) {
        const w = asking.get(it.id);
        if (it.state === "waiting") {
          leaving.delete(it.id);
          asking.set(it.id, { since: w?.since ?? now, title: it.title });
        } else if (w) {
          asking.delete(it.id);
          if (now - w.since >= STEADY) leaving.set(it.id, { since: now, title: it.title });
        }
      }
      for (const m of [asking, leaving]) for (const id of m.keys()) if (!ids.has(id)) m.delete(id);
      draw();
      setTimeout(draw, STEADY + 50);
    },
    /** The session the page shows now. */
    show(id) { shown = id; draw(); },
  };
}
