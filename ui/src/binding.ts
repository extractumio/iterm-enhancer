// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Which iTerm2 window this panel lives in (AC-36). The page cannot see its window (AS-09),
// so it claims the key window when it loads, binds for sure when the user acts in it, and
// keeps the binding across in-page reloads. States of other windows are not its business.

import { api, type TermState } from "./api";

const STORE = "fb.window";
const read = () => { try { return sessionStorage.getItem(STORE); } catch { return null; } };

type Level = "tentative" | "restored" | "confirmed";
interface Claim { window: string | null; level: Level | null }

export class Binding {
  window: string | null = read();
  private level: Level | null = null;
  private asked = 0;

  /** Should this panel show state `s`? A bound panel shows only its window's states; an
   *  unbound one follows the focus, except into windows that show no Toolbelt. */
  accepts(s: TermState): boolean {
    if (this.window) return !s.window || s.window === this.window;
    return s.panel !== false;
  }

  /** On (re)connect: keep the window from before an in-page reload, else claim the key one. */
  async claim(): Promise<void> {
    const r = await api<Claim>("POST", "/api/panel/claim", { body: { window: this.window } });
    this.set(r.window, r.level);
  }

  /** The user acted in the panel: its window is key now. Asked until confirmed once, so
   *  later clicks cost nothing (and a fast window switch cannot move a sure binding). */
  async confirm(): Promise<void> {
    if (this.level === "confirmed" || Date.now() - this.asked < 1000) return;
    this.asked = Date.now();
    const r = await api<Claim>("POST", "/api/panel/bind", { body: {} });
    if (r.window) this.set(r.window, r.level); // no answer (no Toolbelt there) keeps what we had
  }

  /** Another panel's claim won (fbd's `unbind` event). */
  lost() { this.set(null, null); }

  private set(w: string | null, level: Level | null) {
    this.window = w;
    this.level = level;
    try { if (w) sessionStorage.setItem(STORE, w); else sessionStorage.removeItem(STORE); } catch { /* private mode */ }
  }
}
