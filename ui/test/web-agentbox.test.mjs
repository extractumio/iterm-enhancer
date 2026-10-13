// The web app's view of a coding agent's input box (AC-52): the two rules around the cursor and
// the status lines below them.
import { test } from "node:test";
import assert from "node:assert/strict";
import { inputBox, statusKey } from "../../bridge/fbbridge/web/static/agentbox.js";

// "-" a rule, "." text (the prompt right below a rule), " " a blank row
const rows = (s) => [...s].map((c, i) => ({ rule: c === "-", blank: c === " ",
  txt: c === "-" ? "─".repeat(20) : c === " " ? "" : s[i - 1] === "-" ? "❯\u00a0hi" : "  text" }));
const first = (rs, i, txt) => { rs[i].txt = txt; return rs; };

test("Claude Code's box: rules around the cursor, the footer below", () => {
  assert.deepEqual(inputBox(rows("....-.-.."), 5), { top: 4, bottom: 6, end: 8 });
  assert.deepEqual(inputBox(rows("..-...-.  "), 4), { top: 2, bottom: 6, end: 7 }, "a few input rows; blank rows end the footer");
  assert.deepEqual(inputBox(rows("..-.-"), 3), { top: 2, bottom: 4, end: 4 }, "no footer");
});

test("no box: no cursor, no rule below or above, the cursor on a rule, or much more below", () => {
  assert.equal(inputBox(rows("..-.-."), null), null);
  assert.equal(inputBox(rows("..-..."), 3), null);
  assert.equal(inputBox(rows("....-."), 2), null);
  assert.equal(inputBox(rows("..-.-."), 2), null);
  assert.equal(inputBox(rows("-.-" + ".".repeat(13)), 1), null, "more than 12 rows below is output, not a footer");
  assert.equal(inputBox(rows("-.-..-.."), 1), null, "a rule in what would be the footer");
  assert.deepEqual(inputBox(rows("-" + ".".repeat(19) + "-.. "), 18), { top: 0, bottom: 20, end: 22 }, "a long input: its top rule 18 rows above the cursor (the owner's screen)");
  assert.deepEqual(inputBox(rows("-" + ".".repeat(19) + "-"), 1), { top: 0, bottom: 20, end: 20 }, "and its bottom rule 19 rows below");
  assert.equal(inputBox(first(rows("-" + ".".repeat(19) + "-"), 1, "  output"), 18), null, "far rules without the prompt below the top one: not a box");
  // taller than the screen (measured: 40 rows in a 25-row window): the top rule and the prompt are in the history
  const hist = (rs) => (k) => rs.at(-k), up = rows("-.."), screen = rows(".".repeat(19) + "-..  ");
  for (const r of screen) r.txt = r.txt.replace("❯\u00a0hi", "  text");
  assert.deepEqual(inputBox(screen, 18, hist(up)), { top: -3, bottom: 19, end: 21 }, "the top rule 3 rows up in the history");
  assert.deepEqual(inputBox(first(structuredClone(screen), 0, "❯\u00a0hi"), 18, hist(rows("-"))), { top: -1, bottom: 19, end: 21 }, "the history's last row is the rule: the prompt is the screen's first row");
  assert.equal(inputBox(screen, 18, hist(rows("-"))), null, "and without it there: not a box");
  assert.equal(inputBox(screen, 18), null, "no history: no box");
  assert.equal(inputBox(screen, 18, hist(first(rows("-.."), 1, "  output"))), null, "the history's rule without the prompt below it: not a box");
  const sent = [...rows("-.."), ...rows("." .repeat(3000)).map((r) => ({ ...r, txt: "⏺ output" }))];
  assert.equal(inputBox(screen, 18, hist(sent)), null, "an input sent long ago (its rule and prompt far up, output since): not a box");
  assert.equal(inputBox(first(structuredClone(screen), 5, "⏺ output"), 18, hist(up)), null, "a screen row not under the prompt: output, not a box");
  assert.equal(inputBox(rows("-.-..-.."), 6, () => { throw new Error("asked"); }), null, "a rule above the cursor on the screen: the history is not asked");
  assert.equal(inputBox(rows("...-" + ".".repeat(13)), 1, () => { throw new Error("asked"); }), null, "more than a footer below: the history is not asked");
});

test("the input's first row is the prompt, a menu between rules is not an input", () => {
  assert.deepEqual(inputBox(first(rows("-.-"), 1, "❯"), 1), { top: 0, bottom: 2, end: 2 }, "an empty input (its space trimmed)");
  assert.deepEqual(inputBox(first(rows("-.-"), 1, "! ls"), 1), { top: 0, bottom: 2, end: 2 }, "a shell command");
  assert.deepEqual(inputBox(first(rows("-.-"), 1, "> fix"), 1), { top: 0, bottom: 2, end: 2 }, "older Claude Code's prompt");
  assert.deepEqual(inputBox(first(rows("-..-"), 1, "❯\u00a01. first"), 2), { top: 0, bottom: 3, end: 3 }, "a typed list item");
  // Claude Code's questions (measured, 2.1.x): tabs, the question, its items; ↓ on item 2 sent → and opened the next question
  const menu = first(rows("-" + ".".repeat(19) + "-. ."), 1, "←  ☒ Not needed  ☐ Tracking DB  ✔ Submit  →");
  assert.equal(inputBox(menu, 9), null, "questions: tabs first");
  assert.equal(inputBox(first(rows("-...-"), 1, "❯ 1. Yes"), 2), null, "a menu's chosen item right below the rule");
  assert.equal(inputBox(first(rows("-...-"), 1, "Do you want to proceed?"), 2), null, "a permission");
});

test("a status line is known again when only its numbers changed", () => {
  assert.equal(statusKey("  ~/p ctx:55% lim:-/56% · 1 shell"), statusKey("~/p ctx:61% lim:-/57% · 2 shell"));
  assert.notEqual(statusKey("⏵⏵ bypass permissions on"), statusKey("⏵⏵ accept edits on"));
});

test("Claude Code's cursor left below its footer: the caret last seen in the box, else the input's end", async () => {
  const { parkedCaret } = await import("../../bridge/fbbridge/web/static/agentbox.js");
  // measured in iTerm2 (Claude Code, tmux -CC, 54 rows): the input in rows 46-48, rules 45 and 50, footer 51-52, cursor (0, 53)
  const screen = () => { const rs = rows("....-...-.. "); rs[7].blank = true; rs[7].txt = ""; rs[5].end = 30; rs[7].end = 0; rs[6].end = 12; return rs; };
  assert.deepEqual(parkedCaret(screen(), 11, null), { row: 7, col: 2 }, "the end of the input's last row (an empty row: after its indent)");
  const rs = screen(); rs[7] = { rule: false, blank: false, txt: "  more", end: 6 };
  assert.deepEqual(parkedCaret(rs, 11, null), { row: 7, col: 6 });
  const seen = { top: 4, bottom: 8, row: 5, col: 9, txt: "❯\u00a0hi" };
  assert.deepEqual(parkedCaret(screen(), 11, seen), { row: 5, col: 9 }, "where it was last seen");
  assert.deepEqual(parkedCaret(screen(), 11, { ...seen, top: 3 }), { row: 7, col: 2 }, "a box seen elsewhere is not this one");
  assert.deepEqual(parkedCaret(screen(), 11, { ...seen, txt: "❯\u00a0hi there" }), { row: 7, col: 2 }, "its row's text changed since (a submit)");
  assert.deepEqual(parkedCaret(screen(), 11, { ...seen, col: 0 }), { row: 5, col: 2 }, "never on the prompt");
  const tall = rows("-" + ".".repeat(16) + "-.. ");
  assert.deepEqual(parkedCaret(tall, 20, null), { row: 16, col: 2 }, "a tall input (16 rows): the end of its last row");
  assert.deepEqual(parkedCaret(tall, 20, { top: 0, bottom: 17, row: 2, col: 4, txt: "  text" }), { row: 2, col: 4 }, "a tall input: where it was seen, 15 rows above its last");
  const above = rows("-."), over = rows("....-.. ");
  for (const r of over) r.txt = r.txt.replace("❯\u00a0hi", "  text");
  assert.deepEqual(parkedCaret(over, 7, null, (k) => above.at(-k)), { row: 3, col: 2 }, "an input taller than the screen: the end of its last row");
  assert.deepEqual(parkedCaret(over, 7, { top: -2, bottom: 4, row: 1, col: 5, txt: "  text" }, (k) => above.at(-k)), { row: 1, col: 5 }, "and where it was seen");
  assert.equal(parkedCaret(screen(), 6, null), null, "the cursor in the box");
  assert.equal(parkedCaret(screen(), 9, null), null, "on a footer row");
  assert.equal(parkedCaret(screen(), -1, null), null, "no cursor");
  assert.equal(parkedCaret(rows("....... "), 7, null), null, "no box");
  assert.equal(parkedCaret(first(rows("-...-.. "), 1, "❯ 1. Yes"), 7, null), null, "below a menu");
  assert.equal(parkedCaret(rows("-.-" + ".".repeat(13) + " "), 16, null), null, "far below the box: output");
});
