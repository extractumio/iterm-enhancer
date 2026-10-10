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
}
