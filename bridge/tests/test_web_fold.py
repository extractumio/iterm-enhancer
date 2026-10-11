# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Folds of a pane's history (AC-56): an edit's diff, a long command and a long output of a
coding agent's tools, rows of encoded data and nearly equal lines; and what never folds."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio  # noqa: E402
import json  # noqa: E402

from fbbridge.web.fold import SCAN_EVERY, History, folds, text_of  # noqa: E402

B64 = "iVBORw0KGgoAAAANSUhEUgAAALgAAABQCAQAAAAALXejAAAAAmJLR0QA/4ePzL8AAAAHdElNRQfqCR0J"


def rows(*lines):
    return [(t, True) for t in lines]


def spans(found, lines):
    return [(f["from"], f["to"], f["label"]) for f in found]


class FoldTest(unittest.TestCase):
    def test_an_edit_diff_folds_under_its_line(self):
        diff = [f"      {k} {'+' if k % 2 else '-'}  line {k}" for k in range(1, 13)]
        r = rows("⏺ Update(src/app.ts)", "  ⎿  Updated src/app.ts with 6 additions and 6 removals", *diff, "", "⏺ Done.")
        self.assertEqual(spans(folds(r), r), [(2, 13, "⋯ 12 lines of diff")])
        short = rows("⏺ Update(a.ts)", "  ⎿  Updated a.ts with 1 addition", "      1 +  x", "      2    y", "", "⏺ ok")
        self.assertEqual(folds(short), [], "a short diff stays")

    def test_a_tools_output_with_blank_lines_its_long_command_and_blank_runs_fold(self):
        # as Claude Code printed it in the owner's pane: a script with blank lines in it, a
        # command it wrapped over rows of its own, and rows its redraw left empty
        script = ["#!/usr/bin/env bash", "# Pure bundle-identity selector.", "set -euo pipefail", "",
                  'CANONICAL_BUNDLE_ID="app.iqtz.iquantize"', "", "is_iquantize_bundle_id() {",
                  '  local bundle_id="$1"', "}", "", "select_iquantize_bundle_id() {",
                  *[f"  step {k}" for k in range(20)], "}"]
        r = rows("⏺ iPhone 14 Pro is connected.", "",
                 "⏺ Bash(cat apps/iquantize/scripts/select-ipad7-bundle-id.sh | head -60; xcrun devicectl device info",
                 "      apps --device 3EB72D0B | grep -i iquantize | head)",
                 "  ⎿  " + script[0], *[("     " + t) if t else "" for t in script[1:]], "",
                 "⏺ Bash(S=/private/tmp/claude-501/scratchpad; DEV=3EB72D0B; ok=0; for i in $(seq 1 120); do",
                 "      if xcrun devicectl device info apps --device $DEV > $S/iphone-apps.txt 2>&1; then ok=1;",
                 "      break; fi; sleep 10; done; if [ $ok = 0 ]; then echo TIMEOUT; exit 3; fi; echo unlocked;",
                 "      grep -o -E app.iqtz $S/iphone-apps.txt | sort -u; ./deploy-ipad7.sh > $S/deploy.log 2>&1;",
                 "      echo deploy exit=$?)",
                 "", "", "", "", "",
                 "  ⎿  exit=1", "     ERROR: The developer disk image could not be mounted.",
                 "", "", "", "", "", "", "", "", "", "",
                 "⏺ The iPhone is locked.")
        found = spans(folds(r), r)
        out = next(f for f in found if "of output" in f[2])
        self.assertEqual(r[out[0] - 9][0], "  ⎿  #!/usr/bin/env bash", "its line and the output's first 8 lines stay")
        self.assertEqual(out[1], 3 + 1 + len(script) - 1, "to the output's last line, over its blank lines")
        self.assertIn((r.index(("      break; fi; sleep 10; done; if [ $ok = 0 ]; then echo TIMEOUT; exit 3; fi; echo unlocked;", True)),
                       r.index(("      echo deploy exit=$?)", True)), "⋯ 3 more lines of the command"), found)
        blanks = [f for f in found if f[2].endswith("blank lines")]
        self.assertEqual([f[2] for f in blanks], ["⋯ 5 blank lines", "⋯ 10 blank lines"], found)

    def test_an_output_scrolling_in_at_a_blank_line_folds_whole(self):
        words = "build test lint pack sign push tag note mail sync copy move link wipe load save scan sort plot read".split()
        script = ["#!/bin/sh", *[f"{w} --all" for w in words], "", *[f"echo {w} done" for w in words]]
        lines = enc("⏺ Bash(cat run.sh)", "  ⎿  " + script[0], *[("     " + t if t else "") for t in script[1:]], "", "⏺ Ran.")
        h = History(100000)
        cut = 2 + 21                                   # the history ends on the script's blank line
        h.take(0, lines[:cut], bulk=False)
        first = h.scan(SCAN_EVERY)
        h.take(cut, lines[cut:], bulk=False)
        later = h.scan(2 * SCAN_EVERY)
        self.assertEqual((first, [(f["n"], f["to"]) for f in later]), ([], [(10, len(script))]))

    def test_blank_rows_fold_only_before_an_agents_next_line(self):
        box = rows("╭ ask ╮", "", "", "", "╭ ask ╮", "", "", "", "╭ ask ╮", "", "", "", "╭ ask ╮", "", "", "", "╭ ask ╮", "", "", "", "╭ ask ╮", "⏺ Done.")
        self.assertEqual([f[2] for f in spans(folds(box), box)], ["⋯ 5 more copies of this line"], "the copies, not the blank rows")
        file = rows("$ cat a.py", "import os", "", "", "", "def main():", "    pass", "$ ")
        self.assertEqual(folds(file), [], "a file's own blank lines stay")

    def test_a_written_file_and_a_long_command_and_output(self):
        r = rows("⏺ Write(notes.md)", "  ⎿  Wrote 9 lines to notes.md", *[f"       {k} text" for k in range(1, 10)], "", "⏺ Done.")
        self.assertEqual(spans(folds(r), r), [(2, 10, "⋯ 9 lines of the file")])
        cmd = rows("⏺ Bash(python3 - <<'EOF'", *[f"      line {k}" for k in range(10)], "      EOF)", "  ⎿  done", "")
        self.assertEqual(spans(folds(cmd), cmd), [(2, 11, "⋯ 10 more lines of the command")])
        out = rows("⏺ Bash(make)", "  ⎿  cc a.c", *[f"     cc file{k}.c -o file{k}.o" for k in range(30)], "", "⏺ Built.")
        self.assertEqual(spans(folds(out), out), [(10, 31, "⋯ 22 more lines of output")])

    def test_rows_iterm_wrapped_are_one_line(self):
        r = [("⏺ Update(a.ts)", True), ("  ⎿  Updated a.ts with 7 additions", True)]
        for k in range(7):
            r += [(f"      {k} +  a long line that iTerm wrapped at the", False), ("width of the window", True)]
        r += [("", True), ("⏺ Done.", True)]
        self.assertEqual(spans(folds(r), r), [(2, 15, "⋯ 7 lines of diff")])

    def test_blobs_and_nearly_equal_lines(self):
        r = rows("  --logo: url('data:image/png;base64,iVBOR", *[B64] * 5, "  ggg==');")
        self.assertEqual(spans(folds(r), r), [(1, 5, "⋯ 400 B of base64 · 5 lines")])
        listing = rows("$ ls", *[f"vpatch/src/file{k}.py" for k in range(40)], "$ ")
        self.assertEqual(spans(folds(listing), listing), [(4, 38, "⋯ 35 similar lines")])

    def test_a_long_base64_line_iterm_wrapped_folds_its_rows_of_data(self):
        r = [("  --logo: url('data:image/png;base64,iVBOR", False), *[(B64, False)] * 6, ("ggg==');", True), ("}", True)]
        self.assertEqual(spans(folds(r), r), [(1, 6, "⋯ 480 B of base64 · 6 lines")])

    def test_a_line_that_says_something_failed_is_never_one_of_many(self):
        log = rows(*[f"GET /api/item/{k} 200 {k}ms" for k in range(12)], "GET /api/item/12 500 error", *[f"GET /api/item/{k} 200 3ms" for k in range(13, 40)], "$ ")
        found = folds(log)
        self.assertTrue(found and all(not (f["from"] <= 12 <= f["to"]) for f in found), found)

    def test_an_input_box_redrawn_into_the_history_folds_to_one_copy(self):
        rule = "─" * 30 + " @critic-rebound ─"
        r = rows("Done.", rule, rule, "-", rule, "", rule, rule, rule, "-", rule, "What is sound", "")
        self.assertEqual(spans(folds(r), r), [(2, 10, "⋯ 6 more copies of this line")])
        few = rows("build", "ok", "build", "ok", "build", "done")
        self.assertEqual(folds(few), [], "three copies stay")

    def test_what_does_not_fold(self):
        unfinished = rows("⏺ Update(a.ts)", "  ⎿  Updated a.ts with 9 additions", *[f"      {k} +  x" for k in range(9)])
        self.assertEqual(folds(unfinished), [], "it may go on after the last row")
        prose = rows("⏺ Here is what I changed:", *[f"  - item {k}" for k in range(30)], "")
        self.assertEqual(len(folds(prose)), 1, "a long list of nearly equal lines folds")
        talk = rows("⏺ First paragraph of the answer.", "  Second line of it.", "", "⏺ Another message.", "")
        self.assertEqual(folds(talk), [])
        rule = rows(*["=" * 60] * 5, "end")
        self.assertEqual([f for f in folds(rule) if "base64" in f["label"]], [], "a rule is not data")

    def test_text_of_an_encoded_line(self):
        self.assertEqual(text_of({"r": [["ab", None, None, 0], [["漢", "", "x"], 3, None, 1]], "e": True}), "ab漢x")


DIFF = ["⏺ Update(src/app.ts)", "  ⎿  Updated src/app.ts with 6 additions and 6 removals",
        *[f"      {k} +  line {k}" for k in range(12)], "", "⏺ Done."]


def enc(*texts):
    return [{"r": [[t, None, None, 0]] if t else [], "e": True} for t in texts]


class HistoryTest(unittest.TestCase):
    def test_a_bulk_load_sends_no_folded_line_and_tells_the_fold(self):
        h = History(10000)
        lines, found = h.take(100, enc(*DIFF), bulk=True)
        self.assertEqual(found, [{"n": 102, "to": 113, "label": "⋯ 12 lines of diff"}])
        self.assertEqual([i for i, line in enumerate(lines) if line is None], list(range(2, 14)))
        self.assertTrue(h.told(102, 113))
        self.assertTrue(h.told(104, 110), "a part of it (its top may have been dropped)")
        self.assertFalse(h.told(100, 113), "nothing outside it")
        again, found = h.take(100, enc(*DIFF), bulk=True)
        self.assertEqual(found, [], "a fold is told once")

    def test_lines_that_scrolled_off_are_sent_and_folded_by_a_later_look(self):
        h = History(10000)
        for k, line in enumerate(enc(*DIFF)):                  # one at a time, as the screen scrolls
            sent, found = h.take(500 + k, [line], bulk=False)
            self.assertEqual((sent, found), ([line], []))
        self.assertTrue(h.due(SCAN_EVERY))
        self.assertEqual(h.scan(SCAN_EVERY), [{"n": 502, "to": 513, "label": "⋯ 12 lines of diff"}])
        self.assertFalse(h.due(SCAN_EVERY + 0.1), "nothing new since")

    def test_a_long_listing_loaded_in_pages_folds_whole(self):
        listing = enc(*["$ ls -R"], *[f"vpatch/ws/pkg{k}/lib/file{k}.js" for k in range(4500)], *["vpatch/ws/x/errors.js"], "$ ")
        h, n = History(10000), len(listing)
        first = n - 1000
        out = h.take(first, listing[first:], bulk=True)[0]
        while first > 0:                                  # the page asks for older pages, newest first
            a = max(0, first - 1000)
            out = h.take(a, listing[a:first], bulk=True)[0] + out
            first = a
        self.assertGreater(sum(line is None for line in out), 4400, "a name with 'error' in it is one of many")
        self.assertTrue(h.told(10, 4400), "back-to-back folds may be asked for together")
        self.assertFalse(h.told(0, 4400), "but not lines outside them")

    def test_it_keeps_only_what_the_browser_keeps(self):
        h = History(100)
        h.take(0, enc(*["x"] * 3000), bulk=False)
        self.assertLessEqual(len(h.text), 100 + 1000)


class Line:
    def __init__(self, text):
        self.text, self.hard_eol = text, True

    def string_at(self, x):
        return self.text[x]

    def style_at(self, x):
        return None


class MirrorTest(unittest.TestCase):
    def test_the_bridge_sends_folds_and_a_fold_s_lines_only_when_asked(self):
        sent, rows = [], [Line(t) for t in ["$ make", *DIFF, "$ "]]
        client = self._client(rows, sent)

        async def go():
            await client.send_hist(client.session, "reset", 0, client.top)
            await client.handle({"t": "unfold", "n": 1, "to": 5})          # not a fold it was told of
            await client.handle({"t": "unfold", "n": 3, "to": 14})
        asyncio.run(go())
        hist, refused, lines = sent
        self.assertEqual(hist["folds"], [{"n": 3, "to": 14, "label": "⋯ 12 lines of diff"}])
        self.assertEqual(sum(line is None for line in hist["lines"]), 12)
        self.assertEqual((refused["lines"], refused["error"]), ([], "These lines are not a fold of this session."))
        self.assertEqual((lines["t"], lines["first"], len(lines["lines"])), ("lines", 3, 12))
        self.assertEqual(text_of(lines["lines"][0]), "      0 +  line 0")

    def _client(self, rows, sent):
        from fbbridge.web import mirror

        class Ws:
            async def send(self, text):
                sent.append(json.loads(text))

        class Hub:
            app = None

        class Session:
            session_id = "s1"
            pause = None

            async def async_get_contents(self, first, count):
                if self.pause:
                    await self.pause()
                return rows[first:first + count]

            async def async_get_line_info(self):
                return type("Info", (), {"overflow": 0})()

        client = mirror.Client(None, Hub(), Ws(), 10000, None)
        client.session, client.top = Session(), len(rows) - 1
        return client

    def test_unfold_a_part_of_a_fold_or_says_why_not(self):
        sent, rows = [], [Line(t) for t in ["$ make", *DIFF, "$ "]]
        client = self._client(rows, sent)

        async def go():
            await client.send_hist(client.session, "reset", 0, client.top)
            await client.handle({"t": "unfold", "n": 6, "to": 14})            # its top dropped from the page
            await client.handle({"t": "unfold", "n": 0, "to": 14})            # more than the fold
        asyncio.run(go())
        part, outside = sent[1], sent[2]
        self.assertEqual((part["first"], len(part["lines"]), "error" in part), (6, 9, False))
        self.assertEqual((outside["lines"], outside["error"]), ([], "These lines are not a fold of this session."))

    def test_lines_read_before_a_clear_are_not_sent(self):
        from fbbridge.web.fold import History
        sent, rows = [], [Line(t) for t in ["$ make", *DIFF, "$ "]]
        client = self._client(rows, sent)

        async def go():
            await client.send_hist(client.session, "reset", 0, client.top)
            sent.clear()

            async def cleared():                                         # the scrollback is cleared while reading
                client.history = History(10000)
            client.session.pause = cleared
            await client.handle({"t": "unfold", "n": 3, "to": 14})
        asyncio.run(go())
        self.assertEqual(sent, [], "stale lines would land in the new history's rows")


if __name__ == "__main__":
    unittest.main()
