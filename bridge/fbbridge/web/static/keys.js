// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// Browser key events -> the bytes a terminal would send (xterm conventions, as iTerm2 sends them).
// The iTerm2 API does not expose terminal modes, so "application cursor keys" is a manual toggle.

const ESC = "\x1b";

const CURSOR = { ArrowUp: "A", ArrowDown: "B", ArrowRight: "C", ArrowLeft: "D", Home: "H", End: "F" };
const TILDE = { Insert: 2, Delete: 3, PageUp: 5, PageDown: 6, F5: 15, F6: 17, F7: 18, F8: 19,
  F9: 20, F10: 21, F11: 23, F12: 24 };
const SS3 = { F1: "P", F2: "Q", F3: "R", F4: "S" };

function modParam(e) {
  return 1 + (e.shiftKey ? 1 : 0) + (e.altKey ? 2 : 0) + (e.ctrlKey ? 4 : 0);
}

export function ctrlChar(ch) {
  const c = ch.toLowerCase();
  if (c >= "a" && c <= "z") return String.fromCharCode(c.charCodeAt(0) - 96);
  return { " ": "\x00", "@": "\x00", "[": ESC, "\\": "\x1c", "]": "\x1d", "^": "\x1e", "6": "\x1e",
    "_": "\x1f", "-": "\x1f", "/": "\x1f", "?": "\x7f" }[c] ?? null;
}

// e.code gives the unshifted key even when Option turned it into a special character on macOS.
function baseChar(e) {
  if (/^Key[A-Z]$/.test(e.code)) return e.code.slice(3).toLowerCase();
  if (/^Digit\d$/.test(e.code)) return e.code.slice(5);
  return e.key.length === 1 ? e.key : null;
}

// Returns the bytes for e, or null to let the browser handle it (text input, Cmd shortcuts).
export function encodeKey(e, appCursor) {
  if (e.metaKey) return null;
  const m = modParam(e);
  const k = e.key;
  if (k in CURSOR) {
    if (m > 1) return `${ESC}[1;${m}${CURSOR[k]}`;
    return (appCursor ? `${ESC}O` : `${ESC}[`) + CURSOR[k];
  }
  if (k in TILDE) return m > 1 ? `${ESC}[${TILDE[k]};${m}~` : `${ESC}[${TILDE[k]}~`;
  if (k in SS3) return m > 1 ? `${ESC}[1;${m}${SS3[k]}` : `${ESC}O${SS3[k]}`;
  switch (k) {
    case "Enter": return e.altKey ? ESC + "\r" : "\r";
    case "Backspace": return e.ctrlKey ? "\x08" : (e.altKey ? ESC + "\x7f" : "\x7f");
    case "Tab": return e.shiftKey ? `${ESC}[Z` : "\t";
    case "Escape": return ESC;
  }
  if (e.ctrlKey && !e.altKey) {
    const ch = baseChar(e);
    return ch ? ctrlChar(e.shiftKey && ch === "-" ? "_" : ch) : null;
  }
  if (e.altKey) {                    // Option as Meta (iTerm2 "Esc+")
    const ch = baseChar(e);
    if (!ch) return null;
    return ESC + (e.shiftKey ? ch.toUpperCase() : ch);
  }
  return null;
}
