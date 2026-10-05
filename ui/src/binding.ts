// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Which iTerm2 window this panel lives in (AC-36). The page cannot see its window (AS-09),
// so it claims the key window when it loads, binds for sure when the user acts in it, and
// keeps the binding across in-page reloads. States of other windows are not its business:
// a panel that could not bind keeps the window of the first state it showed (`anchor`) and
// leaves other windows' states alone, so a focus change never re-reads every panel.

import { api, session, type TermState } from "./api";

const STORE = "fb.window";

type Level = "tentative" | "restored" | "confirmed";
interface Claim { window: string | null; level: Level | null }

export class Binding {
  window: string | null = session.get(STORE);
  private level: Level | null = null;
  private asked = 0;
  private anchor: string | null = null;  // an unbound panel's window: the first one it showed

  /** Should this panel show state `s`? A bound panel shows only its window's states; an
   *  unbound one only those of the window it started on, and none of a window without a
   *  Toolbelt. */
  accepts(s: TermState): boolean {
    if (this.window) return !s.window || s.window === this.window;
    return s.panel !== false && (!this.anchor || !s.window || s.window === this.anchor);
  }

  /** An unbound panel that ignores `s` because it belongs to another window: the user can
   *  click in the panel to bind it (see the note in main.ts). */
  foreign(s: TermState): boolean {
    return !this.window && s.panel !== false && !!this.anchor && !!s.window && s.window !== this.anchor;
  }

  /** `s` is on screen: remember its window as the anchor of a panel that has none. */
  shown(s: TermState) {
    if (!this.window && !this.anchor && s.window) this.anchor = s.window;
  }

  /** On (re)connect: keep the window from before an in-page reload, else claim the key one. */
  async claim(): Promise<void> {
    const r = await api<Claim>("POST", "/api/panel/claim", { body: { window: this.window } });
    this.set(r.window, r.level);
  }

  /** The user acted in the panel: its window is key now. Asked until confirmed once, so
   *  later clicks cost nothing (and a fast window switch cannot move a sure binding).
   *  True when fbd was asked. */
  async confirm(): Promise<boolean> {
    if (this.level === "confirmed" || Date.now() - this.asked < 1000) return false;
    this.asked = Date.now();
    const r = await api<Claim>("POST", "/api/panel/bind", { body: {} });
    if (r.window) this.set(r.window, r.level); // no answer (no Toolbelt there) keeps what we had
    return true;
  }

  /** Another panel's claim won (fbd's `unbind` event). */
  lost() {
    this.anchor = this.window ?? this.anchor;  // it keeps showing that window's pane
    this.set(null, null);
  }

  private set(w: string | null, level: Level | null) {
    this.window = w;
    this.level = level;
    if (w) this.anchor = null;
    session.set(STORE, w);
  }
}
