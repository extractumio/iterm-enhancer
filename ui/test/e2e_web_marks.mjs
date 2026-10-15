// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-57 checks for e2e_panel.mjs: bookmarks in the whole web app (the real page files, under the
// page's own CSP) against a fake bridge (e2e_web_widgets.mjs's): [+] beside a selection, the
// dialog, the marks on the terminal's edge and in the session list, the way back into older
// lines not loaded, into a fold whose lines were never sent and into a rendered Markdown widget,
// lines that are gone, a closed session's bookmark, a refused save, a selection in a widget.
// Alone: node test/e2e_web_marks.mjs [chromium|webkit].

import { fileURLToPath } from "node:url";
import { check, cleanup, results } from "./e2e_harness.mjs";
import { open } from "./e2e_web_widgets.mjs";
import { loose } from "../../bridge/fbbridge/web/static/marktext.js";

const line = (text) => ({ r: [[text, null, null, 0]], e: true });
const PROMPT = "alex@devbox ~/app % ";
const TEXT = Array.from({ length: 3000 }, (_, i) => `out ${i} lorem ipsum`);
TEXT[150] = "needle: the retry loop never resets its counter";
TEXT.splice(300, 11, `${PROMPT}cat README.md`, "# App", "", "A small **tool** that keeps bookmarks.", "", "## Install", "", "- fast", "- small", "", `${PROMPT}echo hi`);
for (let i = 500; i <= 530; i++) TEXT[i] = `similar ${i}`;
TEXT[515] = "folded secret line 515";
TEXT[2200] = "See [the guide](https://docs.example/guide) for the install steps";
TEXT[100] = "find alpha here";
TEXT[2650] = "alpha beta ALPHA";
TEXT[2700] = "x".repeat(250) + " gam";                             // a long row iTerm wrapped, "gamma" across the wrap
TEXT[2701] = "ma end";
const LONG = ["Long answer: the first paragraph says what changes and why it matters to the reader,",   // over 200 letters
  "and it goes on over a second row of the same paragraph with more words in it", "",
  "The second paragraph explains the check that was run on the delivered tree and",
  "ends here with the closing words of the answer"];
TEXT.splice(2400, LONG.length, ...LONG);
const HISTORY = TEXT.map((t, n) => (n === 2700 ? { ...line(t), e: false } : line(t)));
const FOLDS = [{ n: 500, to: 530, label: "⋯ 31 similar lines" }];
const GONE = "These lines are no longer in this session's history.";
const at0 = 1790000000;
const ITEMS = [
  { id: "A", sid: "S1", key: null, n: 140, text: "the retry loop never resets", note: "ask about the retry loop", title: "One", host: null, at: at0 + 1 },
  { id: "B", sid: "S1", key: null, n: 303, text: "A small tool that keeps bookmarks.", note: "rendered", title: "One", host: null, at: at0 + 2 },
  { id: "C", sid: "S1", key: null, n: 515, text: "folded secret line 515", note: "", title: "One", host: null, at: at0 + 3 },
  { id: "D", sid: "S1", key: null, n: 10, text: "never printed anywhere", note: "gone", title: "One", host: null, at: at0 + 4 },
  { id: "E", sid: "S9", key: null, n: 7, text: "from a session that closed", note: "closed one", title: "Old", host: null, at: at0 + 5 },
  { id: "F", sid: "S1", key: null, n: 2200, text: "See the guide for the install steps", note: "guide", title: "One", host: null, at: at0 + 6 },
  { id: "G", sid: "S1", key: null, n: 2300, text: "phantom words", note: "phantom", title: "One", host: null, at: at0 + 7 },
  { id: "H", sid: "S1", key: null, n: 2400, text: LONG.join("\n"), note: "long answer", title: "One", host: null, at: at0 + 8 },
];
const LAYOUT = [{ kind: "window", label: "Window 1", where: "", host: null, wid: "W1", new: "New tab", pool: "", items: [
  { id: "S1", title: "One", sub: ["zsh", "~/p"], index: "1", host: null, profile: "Default", agent: null, shell: true, state: "" },
  { id: "S2", title: "Two", sub: ["zsh", "~/q"], index: "2", host: null, profile: "Default", agent: null, shell: true, state: "" }] }];

// the bridge's search (bookmarks.py find), enough for these checks: the line where the text (its
// first 200 letters and digits, PRINT), or else a 20-letter part of it, starts
function locate(item) {
  if (item.id === "G") return { n: 2300, start: 0, size: 12 };    // as if the bridge found it, the page does not
  const whole = loose(item.text).slice(0, 200);
  let joined = "";
  const starts = TEXT.map((t) => { const s = joined.length; joined += loose(t); return s; });
  const parts = whole.length > 20 ? [...Array.from({ length: Math.floor(whole.length / 20) }, (_, k) => k * 20), whole.length - 20] : [];
  const tries = [[0, whole.length], ...parts.map((o) => [o, 20])];
  for (const [start, size] of tries) {
    const i = joined.indexOf(whole.slice(start, start + size));
    if (i < 0) continue;
    let n = 0;
    while (n + 1 < starts.length && starts[n + 1] <= i) n++;
    return { n, start, size };
  }
  return { n: null, why: GONE };
}

function bridge(items) {
  let k = 0;
  return (msg, say) => {
    if (msg.t === "more") {
      const first = Math.max(0, msg.to ?? msg.before - 1000);
      const lines = HISTORY.slice(first, msg.before).map((l, j) => (FOLDS.some((f) => first + j >= f.n && first + j <= f.to) ? null : l));
      say({ t: "hist", mode: "prepend", sid: "S1", first, oldest: 0, truncated: false, lines, folds: FOLDS.filter((f) => f.to >= first && f.n < msg.before) });
    }
    if (msg.t === "mark" && msg.note === "FAIL") say({ t: "marked", ref: msg.ref, error: "300 bookmarks are kept at most: delete some first." });
    else if (msg.t === "mark" && !msg.id) {
      const item = { id: `N${++k}`, sid: msg.sid, key: null, n: msg.n, text: msg.text, note: msg.note, title: "One", host: null, at: at0 + 100 + k };
      items.set(item.id, item);
      say({ t: "marked", ref: msg.ref, id: item.id });
      say({ t: "mark", item });
    }
    if (msg.t === "unmark") { items.delete(msg.id); say({ t: "unmark", id: msg.id }); }
    if (msg.t === "locate") say({ t: "located", id: msg.id, sid: "S1", ...locate(items.get(msg.id)) });
    if (msg.t === "find") {                                           // the bridge's search (search.py), line by line
      const q = msg.q.toLowerCase(), hits = [];
      TEXT.forEach((t, n) => {
        if (n === 2701) return;                                       // the rest of row 2700's line
        const text = (n === 2700 ? t + TEXT[2701] : t).toLowerCase();
        let i = -1, k = 0;
        while ((i = text.indexOf(q, i + 1)) >= 0) hits.push([n, k++]);
      });
      say({ t: "found", ref: msg.ref, sid: "S1", q: msg.q, hits, total: hits.length });
    }
    if (msg.t === "sub" && msg.id === "S2") {                         // a second pane, short
      say({ t: "theme", sid: "S2", theme: { bg: "#1e1e1e", fg: "#d4d4d4", cursor: "#fff", cursorText: "#000", ansi: Array(16).fill("#888"), font: null, size: null } });
      say({ t: "hist", mode: "reset", sid: "S2", first: 0, oldest: 0, truncated: false, lines: TEXT.slice(0, 600).map(line) });
      say({ t: "screen", sid: "S2", full: true, n: 1, cols: 80, rows: 1, ch: [[0, line("two")]] });
    }
  };
}

// the bookmark marked now: its text, whether it is in a widget, and where it is in the terminal
const marked = (page) => page.evaluate(() => {
  const line = document.getElementById("markline"), r = line?.range, t = document.getElementById("term").getBoundingClientRect();
  if (!r || line.hidden) return null;
  const b = r.getBoundingClientRect(), l = line.getBoundingClientRect();
  return { text: r.toString(), shadow: r.startContainer?.getRootNode() instanceof ShadowRoot, top: Math.round(b.top - t.top), h: Math.round(t.height),
    line: l.left < b.left && l.top <= b.top + 1 && l.bottom >= b.bottom - 1 };
});
const inView = (m) => m && m.top >= 0 && m.top < m.h;

async function selectRow(page, text) {
  await page.evaluate((text) => {
    const row = [...document.querySelectorAll("#term .ln")].find((el) => el.textContent.includes(text));
    row.scrollIntoView({ block: "center" });
    const r = document.createRange();
    r.selectNodeContents(row);
    getSelection().removeAllRanges();
    getSelection().addRange(r);
  }, text);
  await page.waitForSelector("#markadd:not([hidden])", { timeout: 3000 }).catch(() => {});
}

export async function webMarks(browser, name = "") {
  const at = `AC-57 web app${name}:`;
  const items = new Map(ITEMS.map((i) => [i.id, { ...i }]));
  const { page, errors, sent, say } = await open(browser, { history: HISTORY, first: 2000, folds: FOLDS, screen: ["done"], hook: bridge(items), layout: LAYOUT });
  say({ t: "marks", items: ITEMS, error: null });

  // a selection: [+] at the terminal's right edge, level with it
  await selectRow(page, "out 2500 lorem");
  const plus = await page.evaluate(() => {
    const b = document.getElementById("markadd").getBoundingClientRect(), t = document.getElementById("term");
    const tr = t.getBoundingClientRect(), s = getSelection().getRangeAt(0).getBoundingClientRect();
    return { visible: !document.getElementById("markadd").hidden, right: Math.round(tr.left + t.clientWidth - b.right), dy: Math.round(b.top + b.height / 2 - (s.top + s.height / 2)) };
  });
  check(`${at} a selection gets [+] at the terminal's right edge, level with it`, plus.visible && plus.right >= 0 && plus.right < 20 && Math.abs(plus.dy) < 12, JSON.stringify(plus));
  const pair = await page.evaluate(() => {
    const c = document.getElementById("markcopy"), a = document.getElementById("markadd");
    const cr = c.getBoundingClientRect(), ar = a.getBoundingClientRect(), cs = getComputedStyle(c);
    return { shown: !c.hidden, above: cr.bottom <= ar.top && ar.top - cr.bottom < 16, sameX: Math.abs(cr.left - ar.left) < 1,
      gray: cs.backgroundColor, radius: getComputedStyle(a).borderTopLeftRadius, other: getComputedStyle(a).borderTopRightRadius };
  });
  check(`${at} a gray Copy button stands just above it; both are blobs, not circles`,
    pair.shown && pair.above && pair.sameX && /^rgb\((\d+), \1, |^rgb\(1[45]\d, 1[56]\d, 1[67]\d\)/.test(pair.gray) && pair.radius !== pair.other, JSON.stringify(pair));
  await page.evaluate(() => { window.copied = []; document.addEventListener("copy", () => window.copied.push(String(getSelection()))); });
  check(`${at} the bookmarks button gives way while Copy and + stand beside a selection`,
    await page.evaluate(() => getComputedStyle(document.getElementById("marksfab")).visibility === "hidden"));
  await page.click("#markcopy");
  await page.waitForTimeout(800);                                  // past the buttons' grace after a selection goes
  check(`${at} Copy copies the selected text and keeps the selection for a bookmark`,
    (await page.evaluate(() => window.copied)).some((c) => c.startsWith("out 2500 lorem ipsum")) && !await page.evaluate(() => document.getElementById("markadd").hidden)
    && (await page.evaluate(() => String(getSelection()))).includes("out 2500 lorem ipsum"),
    JSON.stringify(await page.evaluate(() => window.copied)));
  await page.click("#markadd");
  await page.waitForSelector("#markbox[open]");
  check(`${at} [+] opens the dialog with the selected text`, (await page.textContent("#marktext")).includes("out 2500 lorem ipsum"));
  await page.fill("#marknote", "look again");
  await page.click('#markbox button[value="save"]');
  await page.waitForFunction(() => !document.getElementById("markbox").open, null, { timeout: 5000 }).catch(() => {});
  const mark = sent.find((m) => m.t === "mark" && !m.id);
  check(`${at} Save sends the line, the text and the note; the dialog closes`,
    mark?.sid === "S1" && mark.n === 2500 && mark.text.startsWith("out 2500 lorem ipsum") && mark.note === "look again" && !await page.evaluate(() => document.getElementById("markbox").open), JSON.stringify(mark));
  const rail = await page.evaluate(() => [...document.querySelectorAll("#markrail .mk")].map((b) => ({ top: parseFloat(b.style.top), title: b.title, n: b.dataset.count })));
  check(`${at} the session's bookmarks are marks on the terminal's edge, placed by their lines`,
    rail.length >= 4 && rail.some((m) => m.title === "look again") && rail.at(-1).title === "look again" && rail[0].top < rail.at(-1).top, JSON.stringify(rail));
  const rowsAt = await page.evaluate(() => ["S1", "S2"].map((id) => {
    const it = document.querySelector(`#sessions .item[data-id="${id}"]`), r = (sel) => it.querySelector(sel)?.getBoundingClientRect();
    return { strip: !!it.querySelector(".bmbar"), text: Math.round(r(".txt").left), top: Math.round(r(".txt").top - it.getBoundingClientRect().top),
      state: Math.round(r(".st").left), stripIn: r(".bmbar") ? Math.round(r(".bmbar").right) <= Math.round(r(".txt").left) : null };
  }));
  check(`${at} the session list shows an orange strip beside a session with bookmarks, its row laid out as the others`,
    rowsAt[0].strip && !rowsAt[1].strip && rowsAt[0].stripIn && rowsAt[0].text === rowsAt[1].text && rowsAt[0].top === rowsAt[1].top && rowsAt[0].state === rowsAt[1].state, JSON.stringify(rowsAt));

  // renaming the session (the pencil) keeps its bookmarks: they belong to the pane, which keeps its id
  say({ t: "layout", groups: [{ ...LAYOUT[0], items: [{ ...LAYOUT[0].items[0], title: "Renamed one" }, LAYOUT[0].items[1]] }] });
  await page.waitForFunction(() => document.querySelector('#sessions .item[data-id="S1"] .t')?.textContent === "Renamed one");
  await page.click("#marksbtn");
  const renamed = await page.evaluate(() => ({
    rail: document.querySelectorAll("#markrail .mk").length, strip: !!document.querySelector('#sessions .item[data-id="S1"] .bmbar'),
    fab: document.getElementById("marksfabn").textContent,
    titles: [...document.querySelectorAll("#marklist .mkitem")].filter((b) => !b.querySelector(".closed")).map((b) => b.querySelector(".meta").textContent.split(" · ")[0]) }));
  await page.click("#marksbtn");
  check(`${at} a renamed session keeps its bookmarks (marks, strip, count), listed under its new name`,
    renamed.rail >= 4 && renamed.strip && renamed.fab === String(renamed.titles.length) && renamed.titles.length >= 6 && renamed.titles.every((t) => t === "Renamed one"), JSON.stringify(renamed));

  // the way back: into older lines not loaded, found by its text (its line moved)
  await page.click('#markrail .mk[title="ask about the retry loop"]');
  await page.waitForFunction(() => document.getElementById("markline")?.range?.toString().includes("retry loop"), null, { timeout: 8000 }).catch(() => {});
  let m = await marked(page);
  const more = sent.find((x) => x.t === "more" && x.to !== undefined);
  check(`${at} a mark loads the older lines down to its text in one request and marks it in view, a third from the top`,
    more?.before === 2000 && more.to === 110 && m?.text === "the retry loop never resets" && inView(m) && Math.abs(m.top - m.h / 3) < 40, JSON.stringify({ more, m }));
  check(`${at} the bar shows its note`, (await page.textContent("#mbnote")) === "ask about the retry loop" && await page.isVisible("#markbar"));
  check(`${at} the text keeps its look; a wavy line on its left from its first row to its last marks it`, m?.line === true
    && await page.evaluate(() => !CSS.highlights?.has("bookmark") && getComputedStyle(document.getElementById("markline")).backgroundImage.includes("svg")), JSON.stringify(m));

  // this session's bookmarks from the button at the terminal's top right: a toggle
  await page.click("#marksfab");
  const pane = await page.evaluate(() => ({ open: !document.getElementById("markpane").hidden,
    items: [...document.querySelectorAll("#markpane .mkitem .note")].map((n) => n.textContent),
    count: document.getElementById("marksfabn").textContent }));
  await page.mouse.click(30, 300);                                 // a press elsewhere closes it
  const outside = await page.evaluate(() => document.getElementById("markpane").hidden);
  await page.click("#marksfab");
  await page.click("#marksfab");
  const paneClosed = await page.evaluate(() => document.getElementById("markpane").hidden) && outside;
  check(`${at} the button at the top right opens this session's bookmarks, top to bottom; pressed again it closes them`,
    pane.open && paneClosed && pane.items[0] === "gone" && pane.items.includes("look again") && !pane.items.includes("closed one") && pane.count === String(pane.items.length) && pane.count !== "0",
    JSON.stringify({ pane, paneClosed }));

  // into a rendered Markdown widget, drawn only once it is near
  await page.click('#markrail .mk[title="rendered"]');
  await page.waitForFunction(() => { const r = document.getElementById("markline")?.range; return r?.startContainer?.getRootNode() instanceof ShadowRoot; }, null, { timeout: 8000 }).catch(() => {});
  m = await marked(page);
  await page.waitForTimeout(400);
  const later = await marked(page);
  check(`${at} text in a rendered Markdown widget is marked in its rendered text, in view, and stays put`,
    m?.shadow && /small tool that keeps bookmarks/.test(m.text) && inView(m) && Math.abs(later.top - m.top) < 2, JSON.stringify({ m, later }));

  // into a fold whose lines were never sent
  await page.click("#marksbtn");
  await page.click('#marklist .mkitem:has-text("folded secret line 515")');
  await page.waitForFunction(() => document.getElementById("markline")?.range?.toString() === "folded secret line 515", null, { timeout: 8000 }).catch(() => {});
  m = await marked(page);
  check(`${at} from the list: a bookmark in a fold asks for its lines, opens it and marks the text`,
    sent.some((x) => x.t === "unfold" && x.n === 500) && m?.text === "folded secret line 515" && inView(m), JSON.stringify(m));

  // search (AC-58): the magnifier left of the bookmarks' button opens a field; Find goes to the first match
  const at58 = at.replace("AC-57", "AC-58");
  const fabs = await page.evaluate(() => [document.getElementById("findfab"), document.getElementById("marksfab")].map((b) => !b.hidden && Math.round(b.getBoundingClientRect().left)));
  await page.click("#findfab");
  const focused = await page.evaluate(() => document.activeElement?.id);
  await page.fill("#findq", "alpha");
  await page.click('#findbox button[type="submit"]');
  const findShown = (text) => page.waitForFunction((text) => { const r = [...(CSS.highlights?.get("find") ?? [])][0]; return r && r.toString() === text && document.getElementById("findn").textContent.length; }, text, { timeout: 8000 }).catch(() => {});
  await findShown("alpha");
  const first = await page.evaluate(() => {
    const r = [...CSS.highlights.get("find")][0], t = document.getElementById("term").getBoundingClientRect(), b = r.getBoundingClientRect();
    return { n: document.getElementById("findn").textContent, row: r.startContainer.parentElement.closest(".ln")?.textContent, inView: b.top >= t.top && b.bottom <= t.bottom,
      marks: document.querySelectorAll("#findrail .fm").length, on: document.querySelectorAll("#findrail .fm.on").length };
  });
  check(`${at58} search: the magnifier stands left of the bookmarks' button, opens a focused field, Find says how many and shows the first, older lines loaded`,
    fabs[0] && fabs[1] && fabs[0] < fabs[1] && focused === "findq" && first.n === "1 / 3" && first.row === "find alpha here" && first.inView && first.marks === 2 && first.on === 1,
    JSON.stringify({ fabs, focused, first }));
  await page.click("#findnext");
  await findShown("alpha");
  await page.click("#findnext");
  await page.waitForFunction(() => document.getElementById("findn").textContent === "3 / 3");
  await page.waitForTimeout(800);                                    // the pane idle: nothing marks it again
  const third = await page.evaluate(() => ({ n: document.getElementById("findn").textContent, text: [...(CSS.highlights.get("find") ?? [])][0]?.toString() }));
  await page.click("#findprev");
  await page.waitForFunction(() => document.getElementById("findn").textContent === "2 / 3");
  check(`${at58} search: next and the one before step through the matches, two on a row told apart, case aside`,
    third.n === "3 / 3" && third.text === "ALPHA", JSON.stringify(third));
  await page.fill("#findq", "gamma");
  await page.keyboard.press("Enter");
  await findShown("gamma");
  const wrapped = await page.evaluate(() => {
    const r = [...(CSS.highlights.get("find") ?? [])][0];
    return { n: document.getElementById("findn").textContent, text: r?.toString(), rows: r && r.startContainer.parentElement.closest(".ln") !== r.endContainer.parentElement.closest(".ln") };
  });
  check(`${at58} search: a match across a long row iTerm wrapped is found and marked over both rows`,
    wrapped.n === "1 / 1" && wrapped.text === "gamma" && wrapped.rows, JSON.stringify(wrapped));
  say({ t: "hist", mode: "reset", sid: "S1", first: 2000, oldest: 0, truncated: false, lines: HISTORY.slice(2000) });
  await page.waitForFunction(() => /history changed/.test(document.getElementById("findn").textContent), null, { timeout: 5000 }).catch(() => {});
  check(`${at58} search: the history numbered anew drops the matches and says so`,
    await page.evaluate(() => /history changed/.test(document.getElementById("findn").textContent) && document.getElementById("findrail").hidden));
  await page.fill("#findq", "zzzz");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("findn").textContent === "Not found", null, { timeout: 5000 }).catch(() => {});
  const none = await page.evaluate(() => ({ n: document.getElementById("findn").textContent, rail: document.getElementById("findrail").hidden }));
  await page.click("#findfab");
  const shut = await page.evaluate(() => ({ box: document.getElementById("findbox").hidden, rail: document.getElementById("findrail").hidden, hl: !!CSS.highlights?.get("find")?.size }));
  check(`${at58} search: nothing found says so; the magnifier pressed again closes the field, its marks and its highlight`,
    none.n === "Not found" && none.rail && shut.box && shut.rail && !shut.hl, JSON.stringify({ none, shut }));

  // a part of its text (rendered Markdown lacks a link's address); text not found says so
  await page.click('#marklist .mkitem:has-text("guide")');
  await page.waitForFunction(() => /install steps/.test(document.getElementById("markline")?.range?.toString() ?? ""), null, { timeout: 8000 }).catch(() => {});
  m = await marked(page);
  check(`${at} found by a part of its text, that part is marked`, /for the install steps$/.test(m?.text ?? "") && !/https/.test(m.text) && inView(m), JSON.stringify(m));
  // longer than the 200 letters it is found by: the line covers it all, both paragraphs
  await page.click('#marklist .mkitem:has-text("long answer")');
  await page.waitForFunction(() => /closing words of the answer$/.test(document.getElementById("markline")?.range?.toString() ?? ""), null, { timeout: 8000 }).catch(() => {});
  m = await marked(page);
  const lastRow = await page.evaluate(() => { const row = [...document.querySelectorAll("#term .ln")].find((el) => el.textContent.includes("closing words"));
    const r = row.getClientRects()[0], l = document.getElementById("markline").getBoundingClientRect(); return { rowBottom: Math.round(r.bottom), lineBottom: Math.round(l.bottom) }; });
  check(`${at} a bookmark longer than the letters it is found by: the wavy line covers its whole text, to its last row`,
    /^Long answer/.test(m?.text ?? "") && /closing words of the answer$/.test(m.text) && m.line && lastRow.lineBottom >= lastRow.rowBottom - 1, JSON.stringify({ m, lastRow }));
  await page.click('#marklist .mkitem:has-text("phantom")');
  await page.waitForFunction(() => !document.getElementById("mbwhy").hidden, null, { timeout: 5000 }).catch(() => {});
  check(`${at} its text not on the line the bridge named: the bar says so`, /not found there/.test(await page.textContent("#mbwhy")));

  // the view is the user's again: the end of the output, another pane
  await page.click('#markrail .mk[title="ask about the retry loop"]');
  await page.waitForFunction(() => document.getElementById("markline")?.range?.toString().includes("retry loop"), null, { timeout: 8000 }).catch(() => {});
  await page.click("#bottom");
  say({ t: "screen", sid: "S1", full: false, n: 2, cols: 80, rows: 2, ch: [[0, line("after the jump")]] });
  await page.waitForFunction(() => [...document.querySelectorAll("#screen .ln")].some((el) => el.textContent === "after the jump"));
  await page.waitForTimeout(300);
  check(`${at} after the end-of-output button the view follows the output, not the bookmark`,
    await page.evaluate(() => { const t = document.getElementById("term"); return t.scrollHeight - t.scrollTop - t.clientHeight < 8; }));
  await page.click('#sessions .item[data-id="S2"]');
  await page.waitForFunction(() => [...document.querySelectorAll("#screen .ln")].some((el) => el.textContent === "two"));
  check(`${at} a pane without bookmarks has no bookmarks button`, await page.evaluate(() => document.getElementById("marksfab").hidden));
  check(`${at} another pane: no bar, no mark from the last one`,
    await page.evaluate(() => document.getElementById("markbar").hidden && (document.getElementById("markline")?.hidden ?? true)));
  await page.click('#marklist .mkitem:has-text("ask about the retry loop")');
  await page.waitForFunction(() => document.getElementById("markline")?.range?.toString().includes("retry loop"), null, { timeout: 8000 }).catch(() => {});
  m = await marked(page);
  check(`${at} a bookmark of another pane shows that pane and its text`, m?.text === "the retry loop never resets" && inView(m)
    && await page.evaluate(() => document.querySelector("#sessions .item.on")?.dataset.id === "S1"), JSON.stringify(m));

  // lines that are gone, a session that closed
  await page.click('#marklist .mkitem:has-text("gone")');
  await page.waitForFunction(() => !document.getElementById("mbwhy").hidden, null, { timeout: 5000 }).catch(() => {});
  check(`${at} lines no longer in the history: the bar says so and shows the text and the note`,
    (await page.textContent("#mbwhy")).startsWith(GONE) && (await page.textContent("#mbtext")) === "never printed anywhere" && (await page.textContent("#mbnote")) === "gone");
  const closed = await page.evaluate(() => [...document.querySelectorAll("#marklist .mkitem")].find((b) => b.textContent.includes("closed one"))?.querySelector(".closed")?.textContent);
  await page.click('#marklist .mkitem:has-text("closed one")');
  check(`${at} a closed session's bookmark is listed as closed and shows its text`,
    closed === "closed" && /no longer open/.test(await page.textContent("#mbwhy")) && (await page.textContent("#mbtext")) === "from a session that closed");

  // Delete asks again; a save the Mac refuses keeps the dialog with why
  await page.click("#mbdel");
  check(`${at} Delete pressed once only asks`, !sent.some((x) => x.t === "unmark"));
  await page.click("#mbdel");
  await page.waitForFunction(() => document.getElementById("markbar").hidden, null, { timeout: 3000 }).catch(() => {});
  check(`${at} … pressed again it deletes and the bar closes`, sent.some((x) => x.t === "unmark" && x.id === "E") && await page.evaluate(() => document.getElementById("markbar").hidden));
  await selectRow(page, "out 2600 lorem");
  await page.click("#markadd");
  await page.fill("#marknote", "FAIL");
  await page.click('#markbox button[value="save"]');
  await page.waitForSelector("#markerr:not([hidden])", { timeout: 3000 }).catch(() => {});
  check(`${at} a save the Mac refuses keeps the dialog open and says why`,
    /at most/.test(await page.textContent("#markerr")) && await page.evaluate(() => document.getElementById("markbox").open));
  await page.click('#markbox button[value="cancel"]');
  check(`${at} Cancel closes it`, !await page.evaluate(() => document.getElementById("markbox").open));

  // a selection in a widget's rendered text, by a mouse drag (WebKit keeps a script's range in a
  // shadow root out of the document's selection); the widget is drawn once near the view
  await page.click('#markrail .mk[title="rendered"]');
  await page.waitForFunction(() => { const r = document.getElementById("markline")?.range; return r?.startContainer?.getRootNode() instanceof ShadowRoot; }, null, { timeout: 8000 }).catch(() => {});
  await page.click("#mbclose");
  const box = await page.evaluate(() => {
    const host = [...document.querySelectorAll("#term .ln.wg-host")].find((h) => h.shadowRoot.querySelector('.wg[data-kind="markdown"] p'));
    host.scrollIntoView({ block: "center" });
    getSelection().removeAllRanges();
    const r = host.shadowRoot.querySelector('.wg[data-kind="markdown"] p').getBoundingClientRect();
    return { x: r.left + 1, y: (r.top + r.bottom) / 2, to: r.left + Math.min(r.width - 2, 400) };
  });
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  await page.mouse.move(box.to, box.y, { steps: 8 });
  await page.mouse.up();
  const picked = await page.waitForSelector("#markadd:not([hidden])", { timeout: 3000 }).then(() => true, () => false);
  say({ t: "screen", sid: "S1", full: false, n: 2, cols: 80, rows: 2, ch: [[0, line("more output")]] });
  await page.waitForSelector("#paused:not([hidden])", { timeout: 3000 }).catch(() => {});
  check(`${at} while text in a widget is selected the terminal pauses (its lines stay as selected)`,
    await page.evaluate(() => !document.getElementById("paused").hidden && ![...document.querySelectorAll("#screen .ln")].some((el) => el.textContent === "more output")));
  if (picked) await page.click("#markadd");
  const fromWidget = picked && await page.textContent("#marktext");
  check(`${at} text selected in a rendered widget gets [+] too, with its rendered text`, /^A small tool/.test(fromWidget ?? ""), String(fromWidget));
  if (picked) await page.click('#markbox button[value="cancel"]');

  check(`${at} no page error or CSP violation`, !errors.length && !(await page.evaluate(() => window.csp)).length, JSON.stringify([errors, await page.evaluate(() => window.csp)]));
  await page.close();
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const pw = await import(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
  const engine = process.argv[2] ?? "chromium";
  const browser = await pw[engine].launch();
  try { await webMarks(browser, engine === "chromium" ? "" : ` (${engine})`); } finally { await browser.close(); cleanup(); }
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
}
