// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// A held delete (AC-52): a character at a time first, then, after WORDS_AFTER ms of holding,
// a word at a time, so a long text goes quickly, as in a text field. Pure: the keyboard's
// repeats (or a phone's, which come as inputs without a repeat flag) are told by their times.

export const WORDS_AFTER = 1500;   // ms of holding before whole words go
export const WORD_GAP = 90;        // ms between two words (a keyboard repeats every ~30 ms, a phone ~100)
const STILL = 150;                 // ms between two deletes a phone repeats (~100): quick taps are slower
export const CHAR = "\x7f", WORD = "\x17";   // Ctrl+W: the word before the cursor (Claude Code, bash, zsh)

export class HeldDelete {
  constructor(now = () => performance.now()) { this.now = now; this.start = this.last = -Infinity; this.word = -Infinity; }

  /** What one delete sends: CHAR, WORD, or "" (a repeat between two words). repeat: the key's
   *  own repeat flag (undefined: told by time); words: whether this pane takes WORD as a word. */
  next(repeat, words) {
    const t = this.now();
    const held = repeat ?? t - this.last <= STILL;
    if (!held) this.start = t;                                        // a new press
    this.last = t;
    if (!words || t - this.start < WORDS_AFTER) return CHAR;
    if (t - this.word < WORD_GAP) return "";
    this.word = t;
    return WORD;
  }
}
