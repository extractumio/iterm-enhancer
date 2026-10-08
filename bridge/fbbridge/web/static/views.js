// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Terminal, Files and File (the session list is the drawer). Files is iterm-enhancer's panel
// pinned to the shown session; File is its viewer and editor, where every file opened from Files
// gets a tab. Below WIDE they are three views in the same place; at WIDE and up Files is docked
// on the right and File is a window over the terminal, each closed with its own [x].

const $ = (id) => document.getElementById(id);
const NAMES = ["term", "files", "file"];
const WIDE = matchMedia("(min-width: 1400px)");

export class Views {
  constructor({ send, store, onShow }) {
    Object.assign(this, { send, store });
    this.onShow = onShow;          // the terminal was shown or changed width: it may need a redraw
    this.current = "term";         // the view in the main column below WIDE
    this.docked = store.get("filesDocked", true);   // Files is open on the right at WIDE
    this.floating = false;         // File is open over the terminal at WIDE
    this.filesSrc = "";            // what the Files frame shows: its pane, folder and host
    this.filesError = null;        // why the pane has no Files
    this.filesShown = false;
    this.fileHost = null;          // the host the File frame's files are on
    this.theme = null;             // the shown pane's, for both frames (fbd's is the focused pane's)
    for (const f of ["files", "file"]) $(f).addEventListener("load", () => this.tellTheme($(f)));
    for (const b of document.querySelectorAll("button[data-view]")) b.addEventListener("click", () => this.show(b.dataset.view, true));
    $("sideclose").addEventListener("click", () => this.setDocked(false));
    $("fileclose").addEventListener("click", () => { this.floating = false; this.layout(); });
    WIDE.addEventListener("change", () => {
      // The open view moves between the main column and its wide place.
      if (WIDE.matches && this.current === "file") this.floating = true;
      if (WIDE.matches && this.current === "files") this.setDocked(true);
      if (!WIDE.matches) this.current = this.floating ? "file" : "term";   // docked Files was beside it
      if (WIDE.matches) this.current = "term";
      else this.floating = false;
      this.layout();
    });
    addEventListener("message", (e) => this.onMessage(e));
    this.layout();
  }

  /** Shows a view; a click on a view button at WIDE opens or closes Files or File instead. */
  show(name, clicked = false) {
    if (!WIDE.matches) this.current = name;
    else if (name === "files") return this.setDocked(clicked ? !this.docked : true);
    else if (name === "file") this.floating = clicked ? !this.floating : true;
    else this.floating = false;
    this.layout();
  }

  setDocked(on) {
    this.docked = on;
    this.store.set("filesDocked", on);
    this.layout();
  }

  layout() {
    const wide = WIDE.matches;
    const shown = {
      term: wide || this.current === "term",
      files: wide ? this.docked : this.current === "files",
      file: wide ? this.floating : this.current === "file",
    };
    document.body.classList.toggle("wide", wide);
    document.body.classList.toggle("docked", wide && this.docked);
    for (const n of NAMES) document.querySelector(`button[data-view="${n}"]`).setAttribute("aria-selected", String(shown[n]));
    $("term").hidden = !shown.term;
    // a selection left in the hidden terminal would keep it paused, and iOS its Copy menu up
    const sel = getSelection();
    if (!shown.term && sel?.anchorNode && $("term").contains(sel.anchorNode)) sel.removeAllRanges();
    $("files").hidden = !shown.files || !!this.filesError;
    $("viewnote").hidden = !shown.files || !this.filesError;
    $("viewnote").textContent = this.filesError ?? "";
    $("filewin").hidden = !shown.file;
    document.body.dataset.shown = wide ? "term" : this.current;
    if (shown.files && !this.filesShown) this.send({ t: "files" });   // the pane may have changed folder since
    this.filesShown = shown.files;
    if (shown.term) this.onShow();
  }

  /** Another session is shown: its Files are another folder. */
  sessionChanged() {
    this.filesSrc = "";
    this.filesError = null;
    $("files").removeAttribute("src");
    if (this.filesShown) this.send({ t: "files" });
  }

  setTheme(theme) {
    this.theme = theme;
    for (const f of ["files", "file"]) this.tellTheme($(f));
  }

  tellTheme(frame) {
    if (this.theme && frame.getAttribute("src")) frame.contentWindow?.postMessage({ type: "fb-theme", theme: this.theme }, location.origin);
  }

  /** A command line was sent: docked Files follows the folder it may have changed to. */
  commandSent() {
    clearTimeout(this.refresh);
    this.refresh = setTimeout(() => { if (this.filesShown) this.send({ t: "files" }); }, 700);
  }

  /** The shown pane's folder, host and home, asked now (a "cd" may have moved it): for a path
   *  clicked in the terminal. {error} when it has none, null when the server does not answer. */
  paneInfo() {
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.waiters = this.waiters.filter((w) => w !== done); resolve(null); }, 5000);
      const done = (m) => { clearTimeout(timer); resolve(m); };
      (this.waiters ??= []).push(done);
      this.send({ t: "files" });
    });
  }

  /** The server's answer to "files": the pane's key, folder and host, or why there are none. */
  onFiles(m) {
    for (const w of this.waiters?.splice(0) ?? []) w(m);
    this.filesError = m.error ?? null;
    if (!m.error && this.filesShown) {         // the Files frame loads only while it is shown
      const q = new URLSearchParams({ key: m.key, cwd: m.cwd ?? "" });
      if (m.host) q.set("host", m.host);
      const src = `/fb/?${q}`;
      if (src !== this.filesSrc) $("files").src = this.filesSrc = src;   // reloaded only when it moved
    }
    this.layout();
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
