# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Lines a full-screen program scrolled off its alternate screen (AC-56): the spans the hub
opens and closes, how the history folds them, and the hub's notices and poll."""
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge.web import hub, mirror  # noqa: E402
from fbbridge.web.altspans import MOST, Spans  # noqa: E402
from fbbridge.web.fold import SCAN_EVERY, History  # noqa: E402

W = 202


def enc(*texts):
    return [{"r": [[t, None, None, 0]], "e": True} for t in texts]


class SpansTest(unittest.TestCase):
    def test_a_span_runs_from_the_flag_on_to_where_the_number_was_while_it_was_on(self):
        s = Spans()
        s.grew("s1", 22, W)                          # the shell
        s.open("s1", 22, W)                          # measured in iTerm2: top 22 -> 100 while on -> 104 after exit
        s.grew("s1", 60, W)
        self.assertEqual(s.ranges("s1", 0, 60), [(22, 59)], "told as far as it got while it runs")
        self.assertEqual(s.ranges("s1", 30, 40), [(30, 39)])
        s.grew("s1", 100, W)
        s.close("s1")                                # the shell's 4 lines after the program are not the program's
        s.grew("s1", 104, W)
        self.assertEqual(s.ranges("s1", 0, 1000), [(22, 99)])
        self.assertEqual(s.ranges("s1", 100, 1000), [], "the shell's lines after it")
        self.assertEqual(s.ranges("s2", 0, 1000), [], "another session")
        self.assertFalse(s.running("s1"))

    def test_a_program_that_scrolled_nothing_off_leaves_no_span(self):
        s = Spans()
        s.open("s1", 22, W)
        s.close("s1")                                # less, man, a short look at mc
        self.assertEqual(s.spans["s1"], [])
        s.open("s1", 22, W)
        s.open("s1", 30, W)                          # on already: the span stays where it began
        self.assertEqual(s.spans["s1"], [[22, None]])
        s.close("s1")
        s.close("s1")                                # off already: nothing changes
        self.assertEqual(s.spans["s1"], [[22, 30]])

    def test_a_cleared_scrollback_a_new_width_and_closed_sessions_drop_their_spans(self):
        s = Spans()
        s.open("s1", 500, W)
        s.grew("s1", 900, W)
        s.close("s1")
        s.grew("s1", 3, W)                           # /clear or ⌘K: the numbers start over (measured: 0)
        self.assertEqual(s.ranges("s1", 0, 10000), [])
        s.open("s1", 10, W)
        s.grew("s1", 50, W - 10)                     # the pane narrowed: iTerm2 wraps the history anew
        self.assertEqual((s.ranges("s1", 0, 10000), s.running("s1")), ([], False))
        s.open("s2", 5, W)
        s.keep({"s3"})
        self.assertEqual((s.spans, s.seen), ({}, {}))

    def test_only_the_newest_spans_are_kept(self):
        s = Spans()
        for k in range(MOST + 10):
            s.open("s1", 10 * k, W)
            s.grew("s1", 10 * k + 5, W)
            s.close("s1")
        self.assertEqual(len(s.spans["s1"]), MOST)
        self.assertEqual(s.ranges("s1", 0, 20), [], "the oldest went")


class HistoryTest(unittest.TestCase):
    def _spans(self, a, b):
        s = Spans()
        s.open("s1", a, W)
        s.grew("s1", b, W)
        s.close("s1")
        return s

    def test_a_bulk_load_folds_the_programs_lines_and_sends_none_of_them(self):
        s = self._spans(3, 8)
        h = History(10000, scrolled=lambda lo, hi: s.ranges("s1", lo, hi))
        lines, found = h.take(0, enc("$ mc", "a", "b", *[f"│ file-{k} │" for k in range(5)], "$ ls", "x"), bulk=True)
        self.assertEqual(found, [{"n": 3, "to": 7, "label": "⋯ 5 lines of a full-screen program"}])
        self.assertEqual([i for i, line in enumerate(lines) if line is None], [3, 4, 5, 6, 7])
        self.assertTrue(h.told(3, 7), "Show asks for them")

    def test_lines_a_running_program_scrolls_off_fold_as_far_as_it_got(self):
        s = Spans()
        h = History(10000, scrolled=lambda lo, hi: s.ranges("s1", lo, hi))
        h.take(0, enc("$ claude", "hello"), bulk=False)
        s.open("s1", 2, W)
        h.take(2, enc(*[f"old frame {k}" for k in range(5)]), bulk=False)   # its old frames, redrawn and scrolled
        s.grew("s1", 7, W)
        self.assertEqual(h.scan(SCAN_EVERY), [{"n": 2, "to": 6, "label": "⋯ 5 lines of a full-screen program"}])
        h.take(7, enc(*[f"old frame {k}" for k in range(5, 9)]), bulk=False)
        s.grew("s1", 11, W)
        self.assertEqual(h.scan(2 * SCAN_EVERY), [{"n": 7, "to": 10, "label": "⋯ 4 lines of a full-screen program"}],
                         "the next piece; the page shows the two as one")
        s.close("s1")
        h.take(11, enc("$ ", *[f"⏺ line {k}" for k in range(3)]), bulk=False)
        self.assertEqual(h.scan(3 * SCAN_EVERY), [], "the shell's lines after it stay")
        self.assertTrue(h.told(2, 10))

    def test_the_programs_fold_takes_what_its_lines_would_fold_as(self):
        s = self._spans(1, 40)
        h = History(10000, scrolled=lambda lo, hi: s.ranges("s1", lo, hi))
        lines, found = h.take(0, enc("$ ", *["same line"] * 39, "$ "), bulk=True)
        self.assertEqual([f["label"] for f in found], ["⋯ 39 lines of a full-screen program"], "not 'more copies of this line' inside it")


class Line:
    def __init__(self, text):
        self.text, self.hard_eol = text, True

    def string_at(self, x):
        return self.text[x]

    def style_at(self, x):
        return None


class MirrorTest(unittest.TestCase):
    def test_the_streams_frames_tell_where_a_programs_span_got_and_its_folds_reach_the_page(self):
        """The hub's notices open and close; the frames' tops, as iTerm2 gives them, end the span
        where the program was, and the folds reach the page as it scrolls."""
        sent, rows = [], [Line(t) for t in ["$ ", "x", "y", *[f"frame {k}" for k in range(20)]]]

        class Ws:
            async def send(self, text):
                sent.append(json.loads(text))

        class Session:
            session_id = "s1"
            grid_size = types.SimpleNamespace(width=200, height=2)

            async def async_get_contents(self, first, count):
                return rows[first:first + count]

            async def async_get_line_info(self):
                return types.SimpleNamespace(overflow=0)

        def screen(top):
            coord = types.SimpleNamespace
            return types.SimpleNamespace(number_of_lines=2, line=lambda i: rows[top + i], cursor_coord=coord(x=0, y=top),
                                         _ScreenContents__proto=None, windowed_coord_range=coord(coordRange=coord(start=coord(y=top))))
        h = hub.Hub(types.SimpleNamespace())
        client = mirror.Client(None, h, Ws(), 10000, None)
        client.session = session = Session()

        async def go():
            await client.send_screen(session, screen(1))          # the shell: "x" at the top
            h.alt.open("s1", 1, 200)                               # the program begins: the hub's notice
            client.alt = True
            await client.send_screen(session, screen(3))
            await client.send_screen(session, screen(12))
            await client.send_folds(session, SCAN_EVERY)
            h.alt.close("s1")                                      # it ends: the span ends at the last frame's top
            client.alt = False
            await client.send_screen(session, screen(21))          # the shell's lines after it
            await client.send_folds(session, 2 * SCAN_EVERY)
        asyncio.run(go())
        folds = [m["items"] for m in sent if m["t"] == "folds"]
        self.assertEqual(folds, [[{"n": 1, "to": 11, "label": "⋯ 11 lines of a full-screen program"}]])
        self.assertEqual(h.alt.ranges("s1", 0, 100), [(1, 11)], "the hub's spans, shared with every browser")


class HubTest(unittest.TestCase):
    def test_notices_open_and_close_a_span_and_the_poll_follows_it(self):
        info = types.SimpleNamespace(scrollback_buffer_height=7419, overflow=46567)

        class Session:
            grid_size = types.SimpleNamespace(width=202, height=55)

            async def async_get_line_info(self):
                return info
        app = types.SimpleNamespace(get_session_by_id=lambda sid: Session() if sid == "s1" else None)
        h = hub.Hub(app)

        async def go():
            await h.saw_alt("s1", True)
            await h.saw_alt("gone", True)
            info.scrollback_buffer_height = 7519
            await h.follow_alt()                                   # nobody watches: the poll sees where it got
            info.scrollback_buffer_height = 7523                   # the shell's lines after the program
            await h.saw_alt("s1", False)
        asyncio.run(go())
        self.assertEqual(h.alt.ranges("s1", 0, 100000), [(53986, 54085)])
        self.assertEqual(h.alt.ranges("gone", 0, 100000), [])
        h.alt.keep({"s2"})
        self.assertEqual(h.alt.ranges("s1", 0, 100000), [])


if __name__ == "__main__":
    unittest.main()
