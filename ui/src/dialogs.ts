// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Small modal dialog and context menu, styled by the panel theme.

import { esc } from "./api";

export interface Choice { id: string; label: string; primary?: boolean; danger?: boolean }

/** Modal with a message and buttons. Resolves to the chosen id, or "cancel" on Esc. */
export function ask(title: string, message: string, choices: Choice[]): Promise<string> {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML =
      `<div class="modal" role="dialog" aria-label="${esc(title)}"><div class="mtitle">${esc(title)}</div>` +
      (message ? `<div class="mtext">${esc(message)}</div>` : "") +
      `<div class="mbtns">${choices.map((c) =>
        `<button data-id="${esc(c.id)}" class="btn${c.primary ? " primary" : ""}${c.danger ? " danger" : ""}">${esc(c.label)}</button>`).join("")}</div></div>`;
    const done = (id: string) => { back.remove(); document.removeEventListener("keydown", key, true); resolve(id); };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done("cancel"); }
      if (e.key === "Enter") {
        e.preventDefault(); e.stopPropagation();
        done((choices.find((c) => c.primary) ?? choices[0]).id);
      }
    };
    back.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("button[data-id]");
      if (b) done(b.dataset.id!);
      else if (e.target === back) done("cancel");
    });
    document.addEventListener("keydown", key, true);
    document.body.append(back);
    back.querySelector<HTMLButtonElement>(".btn.primary, .btn")?.focus();
  });
}

export interface MenuItem { id: string; label: string; keys?: string; disabled?: boolean; danger?: boolean }
export type MenuEntry = MenuItem | "-";

/** Context menu at the mouse position. Resolves to the chosen id or null. */
export function menu(x: number, y: number, entries: MenuEntry[]): Promise<string | null> {
  return new Promise((resolve) => {
    document.querySelector(".ctx")?.remove();
    const el = document.createElement("div");
    el.className = "ctx";
    el.setAttribute("role", "menu");
    el.innerHTML = entries.map((e) => e === "-" ? '<div class="sep"></div>' :
      `<div class="mi${e.disabled ? " off" : ""}${e.danger ? " danger" : ""}" data-id="${esc(e.id)}" role="menuitem">` +
      `<span>${esc(e.label)}</span>${e.keys ? `<kbd>${esc(e.keys)}</kbd>` : ""}</div>`).join("");
    document.body.append(el);
    const r = el.getBoundingClientRect();
    el.style.left = `${Math.max(4, Math.min(x, innerWidth - r.width - 4))}px`;
    el.style.top = `${Math.max(4, Math.min(y, innerHeight - r.height - 4))}px`;
    const items = [...el.querySelectorAll<HTMLElement>(".mi:not(.off)")];
    let cur = -1;
    const focus = (i: number) => { items.forEach((m, j) => m.classList.toggle("hot", j === i)); cur = i; };
    const done = (id: string | null) => {
      el.remove();
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", key, true);
      resolve(id);
    };
    const outside = (e: Event) => { if (!el.contains(e.target as Node)) done(null); };
    const key = (e: KeyboardEvent) => {
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") done(null);
      else if (e.key === "ArrowDown") focus((cur + 1) % items.length);
      else if (e.key === "ArrowUp") focus((cur - 1 + items.length) % items.length);
      else if (e.key === "Enter" && cur >= 0) done(items[cur].dataset.id!);
    };
    el.addEventListener("click", (e) => {
      const m = (e.target as HTMLElement).closest<HTMLElement>(".mi");
      if (m && !m.classList.contains("off")) done(m.dataset.id!);
    });
    el.addEventListener("pointermove", (e) => {
      const m = (e.target as HTMLElement).closest<HTMLElement>(".mi:not(.off)");
      if (m) focus(items.indexOf(m));
    });
    setTimeout(() => {
      document.addEventListener("pointerdown", outside, true);
      document.addEventListener("keydown", key, true);
    });
  });
}
