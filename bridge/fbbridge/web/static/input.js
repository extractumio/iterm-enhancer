// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Keyboard, paste and the hot keys panel. Everything typed ends up in send(bytes).
import { ctrlChar, encodeKey } from "./keys.js";
import { selectedText } from "./seltext.js";
import { copyByCommand, keepSelectionAt } from "./touchcopy.js";
import { composing } from "./trace.js";
import { inWidget } from "./widgetui.js";

// Four rows of six keys, grouped by what they do: text and the clipboard (with Upload), control
// keys, the arrows with their modifiers, and moving around. Fn swaps the last two rows for the
// F keys (mc, htop), so the panel never grows. The keyboard has its own floating button.
const HOTKEYS = [
  [["Copy", "copy", "Copy the selected text"], ["Paste", "paste"], ["Upload", "upload", "Upload a file and paste its path"],
    ["⇧↩", "\x1b[13;2u", "Shift+Enter: a new line in apps that tell it from Enter"], ["Tab", "\t"], ["Enter", "\r", "Enter (Return)", "strong"]],
  [["Esc", "\x1b"], ["^C", "\x03", "Interrupt"], ["^D", "\x04", "End of input"], ["^Z", "\x1a", "Suspend"],
    ["^R", "\x12", "Search history"], ["Fn", "fn", "F1–F12 in place of the last two rows"]],
  [["Ctrl", "mod:ctrl"], ["Alt", "mod:alt"], ["←", "arrow:D"], ["↑", "arrow:A"], ["↓", "arrow:B"], ["→", "arrow:C"]],
  [["Home", "\x1b[H"], ["End", "\x1b[F"], ["PgUp", "\x1b[5~"], ["PgDn", "\x1b[6~"], ["⇧←", "\x1b[1;2D", "Shift+Left"],
    ["⇧Tab", "\x1b[Z", "Shift+Tab"]],
];
const FN_ROWS = [
  [["F1", "\x1bOP"], ["F2", "\x1bOQ"], ["F3", "\x1bOR"], ["F4", "\x1bOS"], ["F5", "\x1b[15~"], ["F6", "\x1b[17~"]],
  [["F7", "\x1b[18~"], ["F8", "\x1b[19~"], ["F9", "\x1b[20~"], ["F10", "\x1b[21~"], ["F11", "\x1b[23~"], ["F12", "\x1b[24~"]],
];

export class Input {
  constructor({ kbd, kbdButton, term, panel, pasteDialog, send, options }) {
    Object.assign(this, { kbd, kbdButton, term, panel, pasteDialog, send, options });
    this.mods = { ctrl: false, alt: false };
    this.buildPanel();
    this.bind();
  }

  // path and e: how it was typed and when, for the latency trace (trace.js)
  type(text, path = "other", e = null) { if (text) { this.send(text, path, e); this.options.onTyped(); } }

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
      if (seq !== null) { e.preventDefault(); this.type(this.withMods(seq), "keydown", e); }
    });
    k.addEventListener("input", (e) => {
      if (e.isComposing) return;
      const text = e.inputType === "insertLineBreak" ? "\r" : k.value;
      k.value = "";
      this.type(this.withMods(text.replace(/\n/g, "\r")), "input", e);
    });
    k.addEventListener("compositionstart", composing);
    k.addEventListener("compositionend", (e) => { this.type(k.value, "compose", e); k.value = ""; });
    // The field may lie over the cursor's row (for iOS's long-press Paste): a short tap on it
    // still moves the cursor like a tap on the terminal.
    let down = null;
    k.addEventListener("touchstart", (e) => { const t = e.touches[0]; down = t && { x: t.clientX, y: t.clientY, at: Date.now() }; }, { passive: true });
    k.addEventListener("touchend", (e) => {
      const t = e.changedTouches[0];
      if (down && t && Date.now() - down.at < 350 && Math.hypot(t.clientX - down.x, t.clientY - down.y) < 10
          && !this.options.tapLink?.(t.clientX, t.clientY))       // an address or a path on the row still opens
        this.options.tapMove?.(t.clientX, t.clientY);
      down = null;
    });
    const typing = (on) => {
      this.term.classList.toggle("focused", on);
      this.kbdButton.setAttribute("aria-pressed", String(on));
    };
    k.addEventListener("focus", () => typing(true));
    k.addEventListener("blur", () => typing(false));
    // The button never takes focus itself; iOS opens the keyboard for a focus() made in its click.
    this.kbdButton.addEventListener("mousedown", (e) => e.preventDefault());
    this.kbdButton.addEventListener("click", () => { this.wasTyping = false; document.activeElement === k ? k.blur() : this.focus(); });
    // Terminal text copies with its line ends (the page draws them with CSS, which never copies).
    document.addEventListener("copy", (e) => {
      const text = selectedText(this.term);
      if (text === null) return;
      e.clipboardData.setData("text/plain", text);
      e.preventDefault();
    });
    // A hardware keyboard on a phone or a tablet: iOS gives its keys (⌘V too) only to a focused
    // field, and a long press to select takes the focus away; so after a copy from the terminal
    // the keyboard field gets it back when the user was typing before the selection, or a
    // hardware keyboard is known (a modifier pressed alone, which an on-screen keyboard never
    // sends). Without one, that only brings back the keyboard that was up. (A computer keeps its
    // selection.)
    // ⌘C with terminal text selected copies it whatever has the focus: iOS may keep the focus in
    // the keyboard field, whose own (empty) selection it would copy.
    this.hardKbd = this.wasTyping = false;
    const touchable = (this.touchable = navigator.maxTouchPoints > 0);
    const field = (el) => !!el?.closest?.("input, textarea:not(#kbd), select, [contenteditable]");
    k.addEventListener("keydown", (e) => { if (touchable && ["Meta", "Control", "Alt", "Shift"].includes(e.key)) this.hardKbd = true; });
    document.addEventListener("keydown", (e) => {
      if (!e.metaKey || e.ctrlKey || e.altKey || e.key.toLowerCase() !== "c" || !touchable || field(e.target)) return;
      const text = selectedText(this.term);
      if (!text) return;
      e.preventDefault();
      this.copySelection(text);
    }, true);
    document.addEventListener("copy", () => {
      const at = document.activeElement;
      if (at !== k && !field(at)) this.afterCopy(true);
    });
    document.addEventListener("paste", (e) => {
      if (e.target.closest?.("input, textarea:not(#kbd, #pastetext)")) return;   // let fields paste normally
      const image = imageIn(e.clipboardData);
      if (!image && e.target.id === "pastetext") return;               // text goes into the paste field
      e.preventDefault();
      if (e.target.id === "pastetext") this.pasteDialog.close();
      if (image) this.options.pasteImage(image);
      else this.paste(e.clipboardData.getData("text/plain"));
    });
    // With a mouse, a click that does not end a text selection gives the terminal the keyboard.
    const mouse = matchMedia("(hover: hover) and (pointer: fine)");
    this.term.addEventListener("mouseup", () => { if (mouse.matches && !String(getSelection())) this.focus(); });
    // On a touch screen a short tap opens the keyboard; a long press selects text and a moving
    // finger scrolls, so neither of those does. iOS opens the keyboard only for a focus() made
    // inside the touch handler itself.
    // While the keyboard is up, iOS selects no page text on a long press (it taps instead; the
    // same on a plain page with a focused field). So a finger held still closes the keyboard and
    // selects the word under it, with iOS's handles; a tap on the selection shows Copy.
    let start = null, hold = 0;
    const release = () => { clearTimeout(hold); hold = 0; };
    // A touch on an output widget (AC-56) is the widget's: its buttons, its own selection.
    this.term.addEventListener("touchstart", (e) => {
      const t = e.touches[0];
      start = e.touches.length === 1 && !inWidget(e) ? { x: t.clientX, y: t.clientY, at: Date.now() } : null;
      release();
      if (start && document.activeElement === this.kbd) {
        this.wasTyping = true;                     // a copy of what gets selected gives the focus back (bind)
        hold = setTimeout(() => { if (start) { selectWordAt(this.kbd, this.term, start.x, start.y); start.held = true; } }, 450);
      }
    }, { passive: true });
    k.addEventListener("blur", () => { if (!start) this.wasTyping = false; });   // closed by the user, not by a selection
    this.term.addEventListener("touchmove", (e) => {
      const t = e.touches[0];
      if (start && Math.hypot(t.clientX - start.x, t.clientY - start.y) > 10) { start = null; release(); }
    }, { passive: true });
    this.term.addEventListener("touchcancel", release, { passive: true });
    this.term.addEventListener("touchend", (e) => {
      release();
      if (start?.held) { e.preventDefault(); start = null; return; }   // its click would drop the word
      const tap = start && Date.now() - start.at < 350;
      start = null;
      if (!tap) return;
      const t = e.changedTouches[0];
      const sel = getSelection();
      if (t && sel && !sel.isCollapsed && inSelection(sel, t.clientX, t.clientY)) return;   // iOS shows its Copy menu
      // Without this the tap's emulated mousedown that follows moves focus off the keyboard field.
      e.preventDefault();
      if (sel && !sel.isCollapsed) { sel.removeAllRanges(); return; }   // a tap elsewhere clears a selection first
      if (t && this.options.tapLink?.(t.clientX, t.clientY)) return;    // a tap on an address or a path opens it
      if (t) this.options.tapMove?.(t.clientX, t.clientY);              // on the input: the cursor goes there
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
    this.type(this.options.bracketed() ? `\x1b[200~${clean}\x1b[201~` : clean, "paste");
  }

  // After a copy from the terminal on a touch screen the keyboard field takes the keys back when a
  // hardware keyboard is known or the user was typing before the selection (bind).
  afterCopy(later = false) {
    if (!this.touchable || !(this.hardKbd || this.wasTyping)) return;
    this.wasTyping = false;
    if (later) setTimeout(() => this.focus(), 0); else this.focus();
  }

  // Copies the selection, or `given` text (the Copy button's, taken while it was selected). The
  // clipboard API needs HTTPS (or localhost); elsewhere the older copy command copies a selection.
  async copySelection(given) {
    const text = given ?? selectedText(this.term) ?? String(getSelection());
    if (!text) return this.options.status("Select text first, then press Copy.");
    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        this.afterCopy();                          // ⌘V next
        return this.options.status("Copied.");
      } catch { /* fall through */ }
    }
    const ok = given ? copyByCommand(text) : document.execCommand("copy");
    this.options.status(ok ? "Copied." : "Copying is blocked here; use the system Copy menu.");
  }

  async pasteFromClipboard() {
    // The clipboard API needs HTTPS (or localhost); elsewhere, paste into a field instead.
    if (window.isSecureContext && navigator.clipboard?.read) {
      try {
        for (const item of await navigator.clipboard.read()) {
          const type = item.types.find((t) => t.startsWith("image/"));
          if (type) return this.options.pasteImage(await item.getType(type));
        }
      } catch { /* denied or no image: try text */ }
    }
    if (window.isSecureContext && navigator.clipboard?.readText) {
      try { return this.paste(await navigator.clipboard.readText()); } catch { /* denied: use the field */ }
    }
    this.pasteDialog.showModal();
    this.pasteDialog.querySelector("textarea").focus();
  }

  renderKeys() {
    const frag = document.createDocumentFragment();
    for (const group of this.fn ? [...HOTKEYS.slice(0, 2), ...FN_ROWS] : HOTKEYS) {
      const g = document.createElement("div");
      g.className = "keyrow";
      for (const [label, action, hint, look] of group) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = look ? `key ${look}` : "key";
        b.textContent = label;
        b.dataset.action = action;
        if (hint) b.title = hint;
        if (action.startsWith("mod:") || action === "fn") b.setAttribute("aria-pressed", String(action === "fn" && !!this.fn));
        g.append(b);
      }
      frag.append(g);
    }
    this.panel.querySelector(".keys").replaceChildren(frag);
    this.syncMods();
  }

  buildPanel() {
    this.renderKeys();
    // Buttons never take focus from the terminal, so the iOS keyboard stays up.
    this.panel.addEventListener("mousedown", (e) => { if (e.target.closest("button")) e.preventDefault(); });
    this.panel.addEventListener("click", (e) => {
      const b = e.target.closest("button.key");
      if (!b) return;
      const a = b.dataset.action;
      if (a.startsWith("mod:")) { const m = a.slice(4); this.mods[m] = !this.mods[m]; this.syncMods(); return; }
      if (a === "fn") { this.fn = !this.fn; return this.renderKeys(); }
      if (a === "paste") return this.pasteFromClipboard();
      if (a === "upload") return this.options.pickFile();
      if (a === "copy") return this.copySelection();
      let seq = a;
      if (a.startsWith("arrow:")) seq = (this.options.appCursor() ? "\x1bO" : "\x1b[") + a.slice(6);
      this.type(this.withMods(seq), "hotkey", e);
    });
  }

  syncMods() {
    for (const b of this.panel.querySelectorAll('[data-action^="mod:"]'))
      b.setAttribute("aria-pressed", String(this.mods[b.dataset.action.slice(4)]));
  }
}

/** The first image on a paste event's clipboard, or null. */
function imageIn(data) {
  for (const f of data?.files ?? []) if (f.type.startsWith("image/")) return f;
  for (const it of data?.items ?? []) if (it.kind === "file" && it.type.startsWith("image/")) return it.getAsFile();
  return null;
}

/** A path as a shell word: as it is when nothing in it is special, else in single quotes. */
export function shellWord(path) {
  return /^[\w@%+=:,./-]+$/.test(path) ? path : `'${path.replace(/'/g, "'\\''")}'`;
}

function inSelection(sel, x, y) {
  return [...sel.getRangeAt(0).getClientRects()].some((r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom);
}

function selectWordAt(kbd, term, x, y) {
  const at = document.caretRangeFromPoint?.(x, y);
  kbd.blur();
  if (!at) return;
  const sel = getSelection();
  sel.collapse(at.startContainer, at.startOffset);
  sel.modify("move", "backward", "word");
  sel.modify("extend", "forward", "word");
  if (sel.rangeCount) keepSelectionAt(term, sel.getRangeAt(0).getBoundingClientRect().top);
}
