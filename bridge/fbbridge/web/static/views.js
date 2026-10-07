// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Terminal, Files and File: three views in the same place (the session list is the drawer).
// Files is the File Browser's panel pinned to the shown session; File is its viewer and
// editor, where every file opened from Files gets a tab.

const $ = (id) => document.getElementById(id);
const NAMES = ["term", "files", "file"];

export class Views {
  constructor({ send, onShow }) {
    this.send = send;
    this.onShow = onShow;          // the terminal view came back: it may need a redraw
    this.current = "term";
    this.filesSrc = "";            // what the Files frame shows: its pane, folder and host
    this.fileHost = null;          // the host the File frame's files are on
    for (const b of document.querySelectorAll("button[data-view]")) b.addEventListener("click", () => this.show(b.dataset.view));
    addEventListener("message", (e) => this.onMessage(e));
  }

  show(name) {
    this.current = name;
    for (const n of NAMES) {
      document.querySelector(`button[data-view="${n}"]`).setAttribute("aria-selected", String(n === name));
      $(n).hidden = n !== name;
    }
    $("viewnote").hidden = true;
    document.body.dataset.shown = name;
    if (name === "files") this.send({ t: "files" });   // the pane may have changed folder since
    if (name === "term") this.onShow();
  }

  /** Another session is shown: its Files are another folder. */
  sessionChanged() {
    this.filesSrc = "";
    $("files").removeAttribute("src");
    if (this.current === "files") this.send({ t: "files" });
  }

  /** The server's answer to "files": the pane's key, folder and host, or why there are none. */
  onFiles(m) {
    if (m.error) {
      $("files").hidden = true;
      $("viewnote").textContent = m.error;
      $("viewnote").hidden = this.current !== "files";
      return;
    }
    const q = new URLSearchParams({ key: m.key, cwd: m.cwd ?? "" });
    if (m.host) q.set("host", m.host);
    const src = `/fb/?${q}`;
    if (src !== this.filesSrc) $("files").src = this.filesSrc = src;   // reloaded only when it moved
  }

  /** A file chosen in Files opens as a tab of File. */
  open(path, host) {
    const frame = $("file");
    if (this.fileHost === (host ?? null) && frame.contentWindow && frame.src) {
      frame.contentWindow.postMessage({ type: "fb-open", path }, location.origin);
    } else {
      const q = new URLSearchParams({ view: path });
      if (host) q.set("host", host);
      frame.src = `/fb/?${q}`;
      this.fileHost = host ?? null;
    }
    document.querySelector('button[data-view="file"]').disabled = false;
    this.show("file");
  }

  /** Files asks to open a file; File asks to reveal one in Files. Nothing else is listened to. */
  onMessage(e) {
    if (e.origin !== location.origin || typeof e.data?.path !== "string") return;
    if (e.source === $("files").contentWindow && e.data.type === "fb-open") this.open(e.data.path, e.data.host ?? null);
    if (e.source === $("file").contentWindow && e.data.type === "fb-reveal") {
      this.show("files");
      $("files").contentWindow?.postMessage({ type: "fb-reveal", path: e.data.path }, location.origin);
    }
  }
}
