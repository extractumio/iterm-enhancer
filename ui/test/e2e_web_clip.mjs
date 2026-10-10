// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-52 checks for e2e_panel.mjs: ⌘C and ⌘V of a hardware keyboard on a tablet. iOS gives keys
// only to a focused field, and selecting takes its focus, so after a copy the field gets it back.
import { check } from "./e2e_harness.mjs";
import { open } from "./e2e_web_trace.mjs";

const copyFromTerm = (page) => page.evaluate(() => {
  document.getElementById("kbd").blur();
  getSelection().selectAllChildren(document.querySelector("#screen .ln"));
  document.execCommand("copy");
});

export async function webClip(browser) {
  const tablet = await open(browser, "", { viewport: { width: 1024, height: 1366 }, hasTouch: true });
  await tablet.page.focus("#kbd");
  await copyFromTerm(tablet.page);
  await tablet.page.waitForTimeout(50);
  const before = await tablet.page.evaluate(() => document.activeElement?.id);
  await tablet.page.focus("#kbd");
  await tablet.page.keyboard.press("Meta");                 // iOS's own keyboard never sends a modifier alone
  await copyFromTerm(tablet.page);
  await tablet.page.waitForTimeout(50);
  check("AC-52 web app: on a tablet with a hardware keyboard, a copy gives the keyboard field the focus back for ⌘V (not without one)",
    before !== "kbd" && await tablet.page.evaluate(() => document.activeElement?.id) === "kbd", before);
  // ⌘C with terminal text selected while the keyboard field keeps the focus (iOS 26.4+ may)
  const copied = await tablet.page.evaluate(async () => {
    let got = null;
    document.addEventListener("copy", () => { got = String(getSelection()); }, { once: true });
    document.getElementById("kbd").focus();
    getSelection().selectAllChildren(document.querySelector("#screen .ln"));
    document.getElementById("kbd").dispatchEvent(new KeyboardEvent("keydown", { key: "c", metaKey: true, bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 100));
    return { got, line: document.querySelector("#screen .ln").textContent.trim() };
  });
  check("AC-52 web app: on a tablet ⌘C copies the selected terminal text also with the focus in the keyboard field",
    copied.got?.trim() === copied.line && !!copied.line, JSON.stringify(copied));
  await tablet.page.evaluate(() => { const f = document.getElementById("filter"); f.value = "abc"; f.focus(); f.select(); document.execCommand("copy"); });
  await tablet.page.waitForTimeout(50);
  check("AC-52 web app: a copy in another field leaves the focus there", await tablet.page.evaluate(() => document.activeElement?.id) === "filter");
  await tablet.page.close();

  // a phone, no hardware keyboard: typing, then a long press selects (taking the focus), a copy
  // gives the keyboard back; closing it first (no touch going on) means no keyboard after a copy
  const phone = await open(browser, "", { viewport: { width: 390, height: 844 }, hasTouch: true });
  const touch = (type, x, y) => phone.page.evaluate(([type, x, y]) => {
    const term = document.getElementById("term"), t = new Touch({ identifier: 1, target: term, clientX: x, clientY: y });
    term.dispatchEvent(new TouchEvent(type, { touches: type === "touchcancel" ? [] : [t], changedTouches: [t], bubbles: true }));
  }, [type, x, y]);
  const copyThenFocus = async () => {
    await phone.page.evaluate(() => { getSelection().selectAllChildren(document.querySelector("#screen .ln")); document.execCommand("copy"); });
    await phone.page.waitForTimeout(50);
    return phone.page.evaluate(() => document.activeElement?.id);
  };
  await phone.page.focus("#kbd");
  await touch("touchstart", 100, 200);
  await phone.page.evaluate(() => document.getElementById("kbd").blur());    // the selection takes the focus
  await touch("touchcancel", 100, 200);
  const back = await copyThenFocus();
  await touch("touchstart", 100, 200);
  await touch("touchmove", 100, 400);                                        // a scroll: no touch held still
  await phone.page.evaluate(() => document.getElementById("kbd").blur());    // the user closes the keyboard
  const closed = await copyThenFocus();
  check("AC-52 web app: on a phone a copy of what was selected while typing gives the keyboard back; after the user closed it, not",
    back === "kbd" && closed !== "kbd", JSON.stringify({ back, closed }));
  await phone.page.close();

  const computer = await open(browser, "");
  await computer.page.focus("#kbd");
  await computer.page.keyboard.press("Meta");
  await copyFromTerm(computer.page);
  await computer.page.waitForTimeout(50);
  check("AC-52 web app: on a computer a copy keeps the selection (no focus moved)",
    await computer.page.evaluate(() => document.activeElement?.id !== "kbd" && !getSelection().isCollapsed));
  await computer.page.close();

  // a hot key held down repeats as a keyboard's does; a short press types it once; Enter never repeats
  const keys = await open(browser, "");
  await keys.page.click("#keysbtn");
  const box = async (label) => (await keys.page.$(`#hotkeys button.key:text-is("${label}")`)).boundingBox();
  const hold = async (label, ms) => {
    const r = await box(label);
    await keys.page.mouse.move(r.x + r.width / 2, r.y + r.height / 2);
    await keys.page.mouse.down();
    await keys.page.waitForTimeout(ms);
    await keys.page.mouse.up();
    await keys.page.waitForTimeout(150);
  };
  const count = (data) => keys.sent.filter((m) => m.t === "in" && m.data === data).length;
  await hold("↓", 1000);
  const held = count("\x1b[B");
  await keys.page.waitForTimeout(300);
  const after = count("\x1b[B");
  await hold("↑", 80);
  await hold("Enter", 1000);
  check("AC-52 web app: a hot key held down repeats (after 400 ms, about 30 a second) and stops when let go; a short press types once; Enter does not repeat",
    held >= 12 && held <= 25 && after === held && count("\x1b[A") === 1 && count("\r") === 1, JSON.stringify({ held, after, up: count("\x1b[A"), enter: count("\r") }));
  // a character key held on a keyboard: macOS's press-and-hold sends no repeat in a text field,
  // so the page repeats it; a repeat the system sends takes over
  await keys.page.focus("#kbd");
  await keys.page.keyboard.down("k");                      // the system sends no repeat here
  await keys.page.waitForTimeout(1000);
  await keys.page.keyboard.up("k");
  await keys.page.waitForTimeout(200);
  const ks = keys.sent.filter((m) => m.t === "in" && m.data === "k").length;
  const ksAfter = (await keys.page.waitForTimeout(300), keys.sent.filter((m) => m.t === "in" && m.data === "k").length);
  await keys.page.keyboard.down("j");
  await keys.page.evaluate(() => document.getElementById("kbd").dispatchEvent(new KeyboardEvent("keydown", { key: "j", code: "KeyJ", repeat: true, bubbles: true })));
  await keys.page.waitForTimeout(800);
  await keys.page.keyboard.up("j");
  const js = keys.sent.filter((m) => m.t === "in" && m.data === "j").length;
  check("AC-52 web app: a character key held on a keyboard repeats though the system sends no repeat (macOS press-and-hold), stops when let go, and leaves repeating to the system when it does",
    ks >= 12 && ks <= 25 && ksAfter === ks && js === 1, JSON.stringify({ ks, ksAfter, js }));
  await keys.page.close();
}
