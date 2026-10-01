// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The event stream from fbd. It opens only after fbd proved that it knows the token (AC-07):
// while fbd is not running another program may hold its port. On any error it is closed
// (EventSource would reconnect by itself, with the token in its URL) and opened again after a
// new proof: at once while fbd restarts (AC-34), then less often (up to every 2 s), and
// every 5 s while the proof fails.

import { ApiError, eventsUrl, forgetProof, proveServer } from "./api";

export interface StreamHost {
  on: Record<string, (data: any) => void>;  // event name → handler of its JSON data
  open(): void;                               // connected, or connected again
  lost(unproven: boolean): void;              // fbd is out of reach, or did not prove itself
}

export class Stream {
  private es: EventSource | null = null;
  private retry = 0;
  private delay = 250;
  openedAt = 0;  // when it last opened: a fresh fbd has not heard from the bridge yet

  constructor(private host: StreamHost) {}

  async connect() {
    clearTimeout(this.retry);
    try { await proveServer(); } catch (e) { return this.lost(e instanceof ApiError); }
    const es = (this.es = new EventSource(eventsUrl()));
    for (const [name, fn] of Object.entries(this.host.on)) es.addEventListener(name, (e) => fn(JSON.parse((e as MessageEvent).data)));
    es.onerror = () => { this.close(); forgetProof(); this.lost(false); };
    es.onopen = () => { this.openedAt = Date.now(); this.delay = 250; this.host.open(); };
  }

  /** Stop listening; nothing reconnects until connect() is called. */
  close() {
    clearTimeout(this.retry);
    this.es?.close();
    this.es = null;
  }

  private lost(unproven: boolean) {
    this.host.lost(unproven);
    clearTimeout(this.retry);
    this.retry = window.setTimeout(() => void this.connect(), unproven ? 5000 : this.delay);
    this.delay = Math.min(this.delay * 2, 2000);
  }
}
