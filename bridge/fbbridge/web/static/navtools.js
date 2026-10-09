// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// The session list's tools (AC-52, AC-54): its width, "New window" with a profile, "Merge
// windows", the banner for dropped tmux integrations, and the pencil that names the shown pane.

import { profileDot } from "./nav.js";

const $ = (id) => document.getElementById(id);

/** Whether "Merge windows" would move a tab: two groups share a pool (the bridge's: "" for
 *  iTerm2 windows, a tmux connection for its windows, null for a window that stays alone). */
export function canMerge(groups) {
  const seen = new Set();
  for (const g of groups) {
    if (g.pool == null) continue;
    if (seen.has(g.pool)) return true;
    seen.add(g.pool);
  }
  return false;
}

/** Wires the tools; `current()` is the shown pane's {group, item} or null, `onResize()` refits the terminal. */
export function navTools({ send, store, current, onResize }) {
  // The session list's width, dragged at its border (wide screens), remembered per browser.
  const NAV_MIN = 200, NAV_MAX = 640;
  function setNavWidth(px, save) {
    const w = Math.round(Math.min(NAV_MAX, Math.max(NAV_MIN, px)));
    document.documentElement.style.setProperty("--nav-w", `${w}px`);
    $("navgrip").setAttribute("aria-valuenow", String(w));
    if (save) store.set("navWidth", w);
    onResize();
  }
  {
    const grip = $("navgrip");
    const saved = store.get("navWidth", null);
    if (saved) setNavWidth(saved, false);
    grip.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      const left = $("nav").getBoundingClientRect().left;
      const move = (ev) => setNavWidth(ev.clientX - left, false);
      const up = (ev) => {
        setNavWidth(ev.clientX - left, true);
        cancel();
      };
      const cancel = () => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        grip.removeEventListener("pointercancel", cancel);
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
      grip.addEventListener("pointercancel", cancel);
    });
    grip.addEventListener("keydown", (e) => {
      const step = { ArrowLeft: -16, ArrowRight: 16 }[e.key];
      if (step) { e.preventDefault(); setNavWidth($("nav").getBoundingClientRect().width + step, true); }
    });
  }

  // "New window": iTerm2's terminal profiles to pick from, asked when the button is pressed.
  function showProfiles(names) {
    const box = $("profiles");
    const head = document.createElement("p");
    head.textContent = "Open with profile";
    box.replaceChildren(head, ...names.map((name) => {
      const b = document.createElement("button");
      b.type = "button";
      b.append(profileDot(name), name);
      b.onclick = () => { hideProfiles(); send({ t: "newwindow", profile: name }); };
      return b;
    }));
    box.hidden = false;
    $("newwin").setAttribute("aria-expanded", "true");
  }
  function hideProfiles() { $("profiles").hidden = true; $("newwin").setAttribute("aria-expanded", "false"); }
  $("newwin").onclick = () => { hideMerge(); $("profiles").hidden ? send({ t: "profiles" }) : hideProfiles(); };

  // "Merge windows" asks first: iTerm2 cannot undo it, and a closing window's Files panel goes.
  function showMerge(groups) {
    $("mergewin").hidden = !canMerge(groups);
    if ($("mergewin").hidden) hideMerge();
  }
  function hideMerge() { $("mergebox").hidden = true; $("mergewin").setAttribute("aria-expanded", "false"); }
  $("mergewin").onclick = () => {
    if (!$("mergebox").hidden) return hideMerge();
    hideProfiles();
    $("mergebox").hidden = false;
    $("mergewin").setAttribute("aria-expanded", "true");
    $("mergego").focus();
  };
  $("mergego").onclick = () => { hideMerge(); send({ t: "merge" }); };
  $("mergecancel").onclick = hideMerge;

  // ---------- dropped tmux integrations (AC-54) ----------
  // The bridge detached a tmux client iTerm2 had let go of: the session keeps running on its
  // host; Reattach opens it in iTerm2 again.
  function showDrops(items) {
    $("drops").hidden = !items.length;
    $("drops").replaceChildren(...items.map((d) => {
      const p = document.createElement("p");
      const text = document.createElement("span");
      text.textContent = `tmux integration with ${d.host} dropped. The tmux session ${d.session ?? ""} keeps running there.`;
      const again = document.createElement("button");
      again.type = "button"; again.textContent = "Reattach";
      again.onclick = () => send({ t: "reattach", id: d.id });
      const later = document.createElement("button");
      later.type = "button"; later.textContent = "Dismiss";
      later.onclick = () => send({ t: "dismiss", id: d.id });
      p.append(text, again, later);
      return p;
    }));
  }

  // The pencil names the shown pane: its iTerm2 tab and session, and a tmux pane's window.
  function startRename() {
    const found = current();
    if (!found) return;
    $("renamename").value = found.item.title;
    $("current").hidden = $("rename").hidden = true;
    $("renamebox").hidden = false;
    $("renamename").focus();
    $("renamename").select();
  }
  function endRename(refocus = false) {
    $("renamebox").hidden = true;
    $("current").hidden = false;
    $("rename").hidden = !current();
    if (refocus && !$("rename").hidden) $("rename").focus();     // the field's focus is not lost to the page
  }
  $("rename").onclick = (e) => { e.stopPropagation(); startRename(); };   // not the header's own click
  $("renamecancel").onclick = () => endRename(true);
  $("renamebox").addEventListener("submit", (e) => {
    e.preventDefault();
    send({ t: "rename", id: current()?.item.id, name: $("renamename").value });
    endRename(true);
  });
  $("renamename").addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); endRename(true); } });

  return { showProfiles, showMerge, showDrops, endRename, renaming: () => !$("renamebox").hidden };
}
