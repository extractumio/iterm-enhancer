// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Working through a backend restart or an upgrade (AC-34). A short gap shows only an amber
// dot; when fbd is back the panel re-reads what it shows; a panel of another build reloads
// itself in place (keeping its token and window binding in sessionStorage) once nothing
// is unsaved or in progress. A lazy chunk that is gone means the same: this page is old.

import { session } from "./api";

const GRACE = 3000;
const RELOADED = "fb.reloaded-for";

export interface UpgradeHost {
  busy(): boolean;                 // unsaved tabs, a dialog, an inline edit or a save running
  beforeReload(): Promise<void>;   // persist the workspace
  waiting(text: string): void;     // tell the user an update is waiting for them
}


export class Upgrade {
  private gap = 0;           // grace timer while the event stream is down
  private down = false;
  private ready = false;

  constructor(private host: UpgradeHost) {
    window.addEventListener("unhandledrejection", (e) => {
      const msg = String(e.reason?.message ?? e.reason);
      if (/dynamically imported module|Importing a module script failed/i.test(msg)) this.outdated("chunk");
    });
  }

  /** The event stream failed: call it `down` only if it stays so for GRACE. */
  lost(down: () => void) {
    this.down = true;
    if (!this.gap) this.gap = window.setTimeout(() => { this.gap = 0; down(); }, GRACE);
  }

  /** The event stream is back; true if there was a gap (changes may have been missed). */
  back(): boolean {
    clearTimeout(this.gap);
    this.gap = 0;
    const wasDown = this.down;
    this.down = false;
    return wasDown;
  }

  /** fbd says which build it is (every state carries it). */
  build(b?: string) {
    if (b && b !== __BUILD__) this.outdated(b);
  }

  private outdated(build: string) {
    if (this.ready) return;
    this.ready = true;
    if (session.get(RELOADED) === build) return this.host.waiting("Reload the panel to update"); // no loop
    const attempt = async () => {
      if (this.host.busy()) return this.host.waiting("Update ready — reloads after you save");
      clearInterval(timer);
      session.set(RELOADED, build);
      await this.host.beforeReload().catch(() => {});
      location.reload();
    };
    const timer = window.setInterval(() => void attempt(), 1000);
    void attempt();
  }
}
