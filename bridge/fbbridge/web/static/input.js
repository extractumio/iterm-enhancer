// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Keyboard, paste and the hot keys panel. Everything typed ends up in send(bytes).
import { ctrlChar, encodeKey } from "./keys.js";

// Three rows of equal keys, most used first. A label of null draws the keyboard icon.
const HOTKEYS = [
  [[null, "keyboard", "Show or hide the keyboard"], ["Copy", "copy", "Copy the selected text"],
    ["Paste", "paste"], ["Tab", "\t"], ["^C", "\x03", "Interrupt"], ["^D", "\x04", "End of input"]],
  [["Esc", "\x1b"], ["Ctrl", "mod:ctrl"], ["Alt", "mod:alt"],
    ["←", "arrow:D"], ["↑", "arrow:A"], ["↓", "arrow:B"], ["→", "arrow:C"]],
  [["Home", "\x1b[H"], ["End", "\x1b[F"], ["PgUp", "\x1b[5~"], ["PgDn", "\x1b[6~"],
    ["^Z", "\x1a", "Suspend"], ["^L", "\x0c", "Redraw"], ["^R", "\x12", "Search history"]],
];
const KEYBOARD_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="5" width="16" height="10" rx="2"/>'
  + '<path d="M5 8h1M8 8h1M11 8h1M14 8h1M6 12h8"/></svg>';

export class Input {
  constructor({ kbd, term, panel, pasteDialog, send, options }) {
    Object.assign(this, { kbd, term, panel, pasteDialog, send, options });
    this.mods = { ctrl: false, alt: false };
    this.buildPanel();
    this.bind();
  }

  type(text) { if (text) { this.send(text); this.options.onTyped(); } }

  withMods(text) {
    let out = text;
    if (this.mods.ctrl && text.length === 1) out = ctrlChar(text) ?? text;
    if (this.mods.alt) out = "\x1b" + out;
    if (this.mods.ctrl || this.mods.alt) { this.mods.ctrl = this.mods.alt = false; this.syncMods(); }
    return out;
  }

  bind() {
    const k = this.kbd;
    k.addEventListener("keydown", (e) => {
      if (e.isComposing) return;
      const seq = encodeKey(e, this.options.appCursor());
      if (seq !== null) { e.preventDefault(); this.type(this.withMods(seq)); }
    });
    k.addEventListener("input", (e) => {
      if (e.isComposing) return;
      const text = e.inputType === "insertLineBreak" ? "\r" : k.value;
      k.value = "";
      this.type(this.withMods(text.replace(/\n/g, "\r")));
    });
    k.addEventListener("compositionend", () => { this.type(k.value); k.value = ""; });
    const typing = (on) => {
      this.term.classList.toggle("focused", on);
      this.panel.querySelector('[data-action="keyboard"]')?.setAttribute("aria-pressed", String(on));
    };
    k.addEventListener("focus", () => typing(true));
    k.addEventListener("blur", () => typing(false));
    document.addEventListener("paste", (e) => {
      if (e.target.closest?.("input, textarea:not(#kbd)")) return;   // let fields paste normally
      e.preventDefault();
      this.paste(e.clipboardData.getData("text/plain"));
    });
    // With a mouse, a click that does not end a text selection gives the terminal the keyboard.
    const mouse = matchMedia("(hover: hover) and (pointer: fine)");
    this.term.addEventListener("mouseup", () => { if (mouse.matches && !String(getSelection())) this.focus(); });
    // On a touch screen a short tap opens the keyboard; a long press selects text and a moving
    // finger scrolls, so neither of those does. iOS opens the keyboard only for a focus() made
    // inside the touch handler itself.
    let start = null;
    this.term.addEventListener("touchstart", (e) => {
      const t = e.touches[0];
      start = e.touches.length === 1 ? { x: t.clientX, y: t.clientY, at: Date.now() } : null;
    }, { passive: true });
    this.term.addEventListener("touchmove", (e) => {
      const t = e.touches[0];
      if (start && Math.hypot(t.clientX - start.x, t.clientY - start.y) > 10) start = null;
    }, { passive: true });
    this.term.addEventListener("touchend", (e) => {
      const tap = start && Date.now() - start.at < 350;
      start = null;
      if (!tap) return;
      // Without this the tap's emulated mousedown that follows moves focus off the keyboard field.
      e.preventDefault();
      const sel = getSelection();
      if (sel && !sel.isCollapsed) { sel.removeAllRanges(); return; }   // a tap clears a selection first
      if (document.activeElement !== this.kbd) this.focus();
    });
    const form = this.pasteDialog.querySelector("form");
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const ta = form.querySelector("textarea");
      if (e.submitter?.value === "send") this.paste(ta.value);
      ta.value = "";
      this.pasteDialog.close();
      this.focus();
    });
  }

  focus() { this.kbd.focus({ preventScroll: true }); }

  paste(text) {
    const clean = text.replace(/\r\n?/g, "\n").replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "").replace(/\n/g, "\r");
    this.type(this.options.bracketed() ? `\x1b[200~${clean}\x1b[201~` : clean);
  }

  // Copies the selection. The clipboard API needs HTTPS (or localhost); elsewhere the older
  // copy command does the same for the current selection.
  async copySelection() {
    const text = String(getSelection());
    if (!text) return this.options.status("Select text first, then press Copy.");
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(text); return this.options.status("Copied."); } catch { /* fall through */ }
    }
    const ok = document.execCommand("copy");
    this.options.status(ok ? "Copied." : "Copying is blocked here; use the system Copy menu.");
  }

  async pasteFromClipboard() {
    // The clipboard API needs HTTPS (or localhost); elsewhere, paste into a field instead.
    if (window.isSecureContext && navigator.clipboard?.readText) {
      try { return this.paste(await navigator.clipboard.readText()); } catch { /* denied: use the field */ }
    }
    this.pasteDialog.showModal();
    this.pasteDialog.querySelector("textarea").focus();
  }

  buildPanel() {
    const frag = document.createDocumentFragment();
    for (const group of HOTKEYS) {
      const g = document.createElement("div");
      g.className = "keyrow";
      for (const [label, action, hint] of group) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "key";
        if (label === null) { b.innerHTML = KEYBOARD_ICON; b.setAttribute("aria-label", hint); }  // static markup
        else b.textContent = label;
        b.dataset.action = action;
        if (hint) b.title = hint;
        if (action.startsWith("mod:")) b.setAttribute("aria-pressed", "false");
        g.append(b);
      }
      frag.append(g);
    }
    this.panel.querySelector(".keys").replaceChildren(frag);
    // Buttons never take focus from the terminal, so the iOS keyboard stays up.
    this.panel.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
    this.panel.addEventListener("click", (e) => {
      const b = e.target.closest("button.key");
      if (!b) return;
      const a = b.dataset.action;
      if (a.startsWith("mod:")) { const m = a.slice(4); this.mods[m] = !this.mods[m]; this.syncMods(); return; }
      if (a === "paste") return this.pasteFromClipboard();
      if (a === "copy") return this.copySelection();
      if (a === "keyboard") return document.activeElement === this.kbd ? this.kbd.blur() : this.focus();
      let seq = a;
      if (a.startsWith("arrow:")) seq = (this.options.appCursor() ? "\x1bO" : "\x1b[") + a.slice(6);
      this.type(this.withMods(seq));
    });
  }

  syncMods() {
    for (const b of this.panel.querySelectorAll('[data-action^="mod:"]'))
      b.setAttribute("aria-pressed", String(this.mods[b.dataset.action.slice(4)]));
  }
}
