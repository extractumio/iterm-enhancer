# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Search of a pane's history in the web app (AC-58): every line the page could load, rows iTerm
wrapped as one line, a match told by its row and the matches before it there."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge.web import bookmarks, search  # noqa: E402
from fbbridge.web.search import MAX_HITS, find  # noqa: E402


class FindTest(unittest.TestCase):
    def test_matches_by_row_case_insensitive_and_across_a_wrap(self):
        rows = [(10, "Error: disk", True), (11, "all good", True), (12, "an err", False), (13, "or again, error", True)]
        hits, total = find(rows, "ERROR")
        self.assertEqual((hits, total), ([(10, 0), (12, 0), (13, 0)], 3))

    def test_two_on_a_row_are_told_apart_and_the_count_goes_on_past_the_cap(self):
        self.assertEqual(find([(5, "ab ab ab", True)], "ab"), ([(5, 0), (5, 1), (5, 2)], 3))
        hits, total = find([(n, "x", True) for n in range(MAX_HITS + 5)], "x")
        self.assertEqual((len(hits), total), (MAX_HITS, MAX_HITS + 5))


    def test_a_long_wrapped_line_is_quick(self):
        import time
        rows = [(n, "a" * 200, False) for n in range(11000)] + [(11000, "a", True)]
        t = time.perf_counter()
        hits, total = find(rows, "a")
        self.assertEqual((len(hits), total), (MAX_HITS, 11000 * 200 + 1))
        self.assertLess(time.perf_counter() - t, 0.5)


class Pane:
    session_id = "S1"

    def __init__(self, lines):
        self.lines = lines

    async def async_get_line_info(self):
        return SimpleNamespace(overflow=0, scrollback_buffer_height=len(self.lines) - 2, mutable_area_height=2)

    async def async_get_contents(self, first, count):
        return [SimpleNamespace(string=t, hard_eol=True) for t in self.lines[first:first + count]]


class MessageTest(unittest.TestCase):
    def test_the_answer_and_a_refused_search(self):
        got = []

        async def send(m):
            got.append(m)
        client = SimpleNamespace(session=Pane(["one", "two needle", "three", "needle", "foo\x00bar"]), max_history=10000, send=send)

        async def go():
            search.handle(client, {"t": "find", "ref": "r", "q": "Needle"})
            search.handle(client, {"t": "find", "ref": "e", "q": "  "})
            search.handle(client, {"t": "find", "ref": "z", "q": "foo bar"})
            while bookmarks._tasks:
                await asyncio.gather(*bookmarks._tasks)
        asyncio.run(go())
        by = {m["ref"]: m for m in got}
        self.assertEqual(([tuple(h) for h in by["r"]["hits"]], by["r"]["total"], by["r"]["sid"]), ([(1, 0), (3, 0)], 2, "S1"))
        self.assertIn("characters", by["e"]["error"])
        self.assertEqual(by["z"]["total"], 1, "a cell the program skipped is a space, as the page draws it")


if __name__ == "__main__":
    unittest.main()
