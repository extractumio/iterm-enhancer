// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Dragging in the session list (AC-52): a row by its grip within its group (iTerm2 reorders
// that window's tabs), a group by its grip among the groups (this browser's list only).
// Pointer events, so a finger drags as a mouse does (iOS Safari's own drag and drop is not).

/** Starts a drag at a grip in `list`; `done(kind, container, ids)` gets the new order. */
export function bindReorder(list, done, ended = () => {}) {
  let drag = null;
  list.addEventListener("pointerdown", (e) => {
    const grip = e.target.closest(".grip");
    if (!grip || e.button > 0) return;
    const el = grip.closest(grip.dataset.kind === "group" ? "section" : "li");
    if (!el) return;
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    drag = { el, kind: grip.dataset.kind, grip, start: [...el.parentElement.children].indexOf(el) };
    el.classList.add("dragging");
    list.classList.add("reordering");
  });
  list.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const parent = drag.el.parentElement;
    const over = [...parent.children].find((c) => {
      if (c === drag.el) return false;
      const r = c.getBoundingClientRect();
      return e.clientY >= r.top && e.clientY <= r.bottom;
    });
    if (!over) return;
    const r = over.getBoundingClientRect();
    parent.insertBefore(drag.el, e.clientY < r.top + r.height / 2 ? over : over.nextSibling);
  });
  const end = (commit) => {
    if (!drag) return;
    const { el, kind, start } = drag;
    drag = null;
    el.classList.remove("dragging");
    list.classList.remove("reordering");
    const parent = el.parentElement;
    if (!commit) parent.insertBefore(el, [...parent.children].filter((c) => c !== el)[start] ?? null);   // back where it was
    else if ([...parent.children].indexOf(el) !== start) {
      const ids = [...parent.children].map((c) => (kind === "group" ? c.dataset.wid : c.querySelector(".item")?.dataset.id)).filter(Boolean);
      done(kind, parent, ids);
    }
    ended();
  };
  list.addEventListener("pointerup", () => end(true));
  list.addEventListener("pointercancel", () => end(false));       // the system took the touch: nothing moves
  return { dragging: () => drag !== null };
}

/** Groups in the order this browser keeps (by iTerm2 window id); windows it has not seen go last. */
export function ordered(groups, order) {
  const at = (g) => { const i = order.indexOf(g.wid); return i < 0 ? Infinity : i; };
  return groups.map((g, i) => [g, i]).sort((a, b) => at(a[0]) - at(b[0]) || a[1] - b[1]).map(([g]) => g);
}
