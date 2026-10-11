# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Bookmarks of the web app's terminal (AC-57): found again by their text nearest their line
(iTerm2 numbers lines anew when it wraps them again), kept on the Mac in a file only the user
reads, stamped by the bridge, and moved along with a tmux pane attached anew."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge.web import bookmarks, mirror  # noqa: E402
from fbbridge.web.bookmarks import GONE, MAX_ITEMS, MarkError, Store, find, loose  # noqa: E402
from fbbridge.web.hub import Hub  # noqa: E402

CASES = Path(__file__).with_name("loose_cases.json")


def run(coro):
    return asyncio.run(coro)


class Pane:
    """An iTerm2 session: its lines from 0, all of them loadable; the last `screen` the screen."""

    def __init__(self, sid, lines, screen=5, tmux=False):
        self.session_id, self.lines, self.screen, self.tmux = sid, lines, screen, tmux

    async def async_get_line_info(self):
        return SimpleNamespace(overflow=0, scrollback_buffer_height=len(self.lines) - self.screen, mutable_area_height=self.screen)

    async def async_get_contents(self, first, count):
        return [SimpleNamespace(string=t) for t in self.lines[first:first + count]]

    async def async_get_variable(self, name):
        return "client" if name == "tmuxRole" and self.tmux else ""


class Browser:
    def __init__(self, hub, session, conn=None, open_ids=("S1", "S2", "S9")):
        self.hub, self.session, self.conn, self.max_history = hub, session, conn, 10000
        self.open = set(open_ids)
        self.app = SimpleNamespace(get_session_by_id=lambda sid: sid in self.open or None)
        self.got = []
        hub.clients.add(self)

    async def send(self, msg):
        self.got.append(msg)

    async def send_quietly(self, text):
        self.got.append(json.loads(text))


def layout(*items):
    return [{"items": [{"id": sid, "title": title, "host": host} for sid, title, host in items]}]


class LooseTest(unittest.TestCase):
    def test_the_same_cases_as_the_page(self):
        for text, want in json.loads(CASES.read_text()):
            self.assertEqual(loose(text), want, text)


class FindTest(unittest.TestCase):
    def test_text_across_a_wrap_and_lines_is_found_where_it_starts(self):
        rows = [(10, "$ make"), (11, "error: the retry loop ne"), (12, "ver resets its counter"), (13, "$ ")]
        self.assertEqual(find(rows, "retry loop never resets\nits counter", 3), (11, 0, 30))

    def test_the_place_nearest_the_line_wins(self):
        rows = [(n, "$ npm test" if n in (5, 50, 95) else f"out {n}") for n in range(100)]
        self.assertEqual(find(rows, "$ npm test", 60)[0], 50)
        self.assertEqual(find(rows, "$ npm test", 90)[0], 95)

    def test_gone_text_is_none_and_text_without_letters_keeps_its_line(self):
        rows = [(1, "a"), (2, "b")]
        self.assertIsNone(find(rows, "never printed", 1))
        self.assertEqual(find(rows, "──── ", 2), (2, 0, 0))
        self.assertIsNone(find(rows, "──── ", 7))

    def test_a_long_run_of_one_letter_is_quick(self):
        import time
        rows = [(n, "0" * 200) for n in range(10000)]
        t = time.perf_counter()
        self.assertEqual(find(rows, "0" * 300, 5000)[0], 5000)
        self.assertIsNone(find(rows, "abc" * 30, 5000))
        self.assertLess(time.perf_counter() - t, 0.2)

    def test_the_part_found_nearest_the_line_wins(self):
        rows = [(1, "for the install steps and more"), *[(n, "x") for n in range(2, 90)],
                (90, "See [the guide](https://docs.example/guide) for the install steps and more")]
        rendered = "See the guide for the install steps and more"
        self.assertEqual(find(rows, rendered, 90)[0], 90)
        self.assertEqual(find(rows, rendered, 3)[0], 1)

    def test_rendered_markdown_is_found_by_its_start(self):
        rows = [(1, "See [the guide](https://docs.example/guide) for the install steps and more")]
        rendered = "See the guide for the install steps and more"
        n, start, size = find(rows, rendered, 1)
        self.assertEqual((n, size), (1, 20))
        self.assertIn(loose(rendered)[start:start + size], loose(rows[0][1]))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "web-bookmarks.json"

    def test_kept_across_a_restart_readable_by_the_user_only(self):
        async def go():
            s = Store(self.path)
            a = await s.add({"sid": "S1", "key": None, "n": 4, "text": "x", "note": "n", "title": "t", "host": None})
            await s.change(a["id"], note="later")
            b = await s.add({"sid": "S2", "key": None, "n": 9, "text": "y", "note": "", "title": "u", "host": "devbox.example"})
            await s.remove(b["id"])
            return a
        a = run(go())
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        again = Store(self.path).all()
        self.assertEqual([(i["id"], i["note"], i["sid"]) for i in again], [(a["id"], "later", "S1")])

    def test_an_unreadable_file_is_left_as_it_is_and_saves_say_why(self):
        self.path.write_text("{not json")
        s = Store(self.path)
        self.assertIn("cannot be read", s.broken)
        with self.assertRaisesRegex(MarkError, "move it away"):
            run(s.add({"sid": "S1", "key": None, "n": 1, "text": "x", "note": "", "title": "", "host": None}))
        self.assertEqual(self.path.read_text(), "{not json")

    def test_the_cap_and_a_failed_write_keep_nothing_half_saved(self):
        s = Store(self.path)
        s.items = {str(k): {"id": str(k)} for k in range(MAX_ITEMS)}
        with self.assertRaisesRegex(MarkError, "at most"):
            run(s.add({"sid": "S"}))
        s = Store(self.path)
        with mock.patch.object(bookmarks.setup_state, "write", side_effect=OSError(28, "No space left on device")):
            with self.assertRaisesRegex(MarkError, "No space left"):
                run(s.add({"sid": "S", "n": 1, "text": "x"}))
        self.assertEqual(s.all(), [])


class MessagesTest(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(SimpleNamespace(), marks=Path(tempfile.mkdtemp()) / "web-bookmarks.json")
        self.hub.layout = layout(("S1", "api refactor", "devbox.example"), ("S2", "other", None))
        self.pane = Pane("S1", [f"line {k}" for k in range(40)] + ["the retry loop never resets", "$ "])
        self.me, self.other = Browser(self.hub, self.pane), Browser(self.hub, Pane("S2", []))

    def marked(self):
        return next(m for m in reversed(self.me.got) if m["t"] == "marked")["id"]

    def handle(self, msg, who=None):
        if msg["t"] == "mark":
            self.refs = getattr(self, "refs", 0) + 1
            msg = {**msg, "ref": msg.get("ref", "r") + str(self.refs)}

        async def go():
            await bookmarks.handle(who or self.me, msg)
            while bookmarks._tasks:                 # a locate runs on a task of its own
                await asyncio.gather(*bookmarks._tasks)
            await asyncio.sleep(0)                  # the broadcasts
        run(go())

    def test_a_new_bookmark_is_stamped_by_the_bridge_and_told_to_every_browser(self):
        self.handle({"t": "mark", "ref": "r1", "sid": "S1", "n": 40, "text": "the retry loop", "note": " ask ", "title": "forged", "host": "evil"})
        reply = next(m for m in self.me.got if m["t"] == "marked")
        self.assertEqual((reply["t"], reply["ref"]), ("marked", "r11"))
        item = self.hub.marks.items[reply["id"]]
        self.assertEqual({k: item[k] for k in ("sid", "key", "n", "note", "title", "host")},
                         {"sid": "S1", "key": None, "n": 40, "note": "ask", "title": "api refactor", "host": "devbox.example"})
        self.assertIn({"t": "mark", "item": item}, self.other.got)

    def test_a_bookmark_for_a_pane_not_shown_or_without_text_is_refused_with_why(self):
        self.handle({"t": "mark", "ref": "r", "sid": "S2", "n": 1, "text": "x", "note": ""})
        err = lambda: [m for m in self.me.got if m["t"] == "marked"][-1]["error"]  # noqa: E731
        self.assertIn("no longer the one shown", err())
        self.handle({"t": "mark", "ref": "r", "sid": "S1", "n": 1, "text": "  ", "note": ""})
        self.assertIn("Select some text", err())
        self.handle({"t": "mark", "ref": "r", "sid": "S1", "n": 1, "text": "x", "note": "y" * 1001})
        self.assertIn("longer than", err())
        self.assertEqual(self.hub.marks.all(), [])
        self.assertFalse([m for m in self.other.got if m["t"] == "mark"])

    def test_a_closed_session_is_refused_and_a_save_sent_again_is_kept_once(self):
        self.me.open.discard("S1")
        self.handle({"t": "mark", "sid": "S1", "n": 40, "text": "the retry loop", "note": ""})
        self.assertIn("has closed", self.me.got[-1]["error"])
        self.me.open.add("S1")

        async def twice():
            msg = {"t": "mark", "ref": "same", "sid": "S1", "n": 40, "text": "the retry loop", "note": ""}
            await bookmarks.handle(self.me, msg)
            await bookmarks.handle(self.me, msg)
        run(twice())
        ids = [m["id"] for m in self.me.got if m["t"] == "marked" and m.get("ref") == "same"]
        self.assertEqual((len(ids), len(set(ids)), len(self.hub.marks.all())), (2, 1, 1))

    def test_found_again_after_iterm2_wrapped_the_history_anew(self):
        self.handle({"t": "mark", "ref": "r", "sid": "S1", "n": 40, "text": "the retry loop never resets", "note": ""})
        mid = self.marked()
        self.pane.lines = [f"l{k}" for k in range(70)] + ["the retry loop ne", "ver resets", "$ "]   # narrower: more rows
        self.handle({"t": "locate", "id": mid})
        located = [m for m in self.me.got if m["t"] == "located"]
        self.assertEqual(located, [{"t": "located", "id": mid, "sid": "S1", "n": 70, "start": 0, "size": 23}])
        self.assertEqual(self.hub.marks.items[mid]["n"], 70, "looked for there first next time")
        self.assertIn({"t": "mark", "item": self.hub.marks.items[mid]}, self.other.got)

    def test_gone_lines_and_another_pane_say_why(self):
        self.handle({"t": "mark", "ref": "r", "sid": "S1", "n": 40, "text": "the retry loop", "note": ""})
        mid = self.marked()
        self.pane.lines = ["$ clear", "$ "]
        self.handle({"t": "locate", "id": mid})
        self.assertEqual((self.me.got[-1]["n"], self.me.got[-1]["why"]), (None, GONE))
        self.handle({"t": "locate", "id": mid}, self.other)
        self.assertIn("not the one shown", self.other.got[-1]["why"])
        self.handle({"t": "unmark", "id": mid}, self.other)
        self.assertEqual(self.hub.marks.all(), [])
        self.assertIn({"t": "unmark", "id": mid}, self.me.got)

    def test_a_tmux_pane_attached_anew_takes_its_bookmarks(self):
        tmux = Pane("S1", ["x"], tmux=True)
        self.me.session = tmux
        with mock.patch.object(bookmarks, "resolve", side_effect=_done({"key": "tmux:devbox:/tmp/s:%7"})):
            self.handle({"t": "mark", "ref": "r", "sid": "S1", "n": 0, "text": "x", "note": ""})
            mid = self.marked()
            self.assertEqual(self.hub.marks.items[mid]["key"], "tmux:devbox:/tmp/s:%7")
            self.hub.layout = layout(("S9", "api", None), ("S2", "other", None))   # S1 closed, S9 is the pane again
            run(bookmarks.rebind(self.me, Pane("S9", ["x"], tmux=True)))
        self.assertEqual(self.hub.marks.items[mid]["sid"], "S9")


def _done(value):
    async def f(*_):
        return value
    return f


class RowsTest(unittest.TestCase):
    def test_a_short_chunk_shifts_no_other_line(self):
        pane = Pane("S1", [f"l{k}" for k in range(1300)], screen=0)
        real = pane.async_get_contents

        async def short(first, count):
            got = await real(first, count)
            return got[:-1] if first == 0 else got     # iTerm2 dropped a line meanwhile
        pane.async_get_contents = short
        rows = run(bookmarks.history_rows(pane, 10000))
        self.assertTrue(all(t == f"l{n}" for n, t in rows))
        near = run(bookmarks.history_rows(pane, 10000, around=1250))
        self.assertEqual((near[0][0], near[-1][0]), (250, 1299))


class OlderLinesTest(unittest.TestCase):
    def test_older_lines_down_to_a_bookmark_in_one_page(self):
        sent = []
        c = mirror.Client.__new__(mirror.Client)
        c.session, c.top, c.overflow, c.max_history = Pane("S1", []), 9000, 0, 10000

        async def send_hist(session, mode, first, upto):
            sent.append((mode, first, upto))
        c.send_hist = send_hist
        run(c.send_older(8000, 2500))
        run(c.send_older(8000))
        run(c.send_older(8000, -50))
        self.assertEqual(sent, [("prepend", 2500, 8000), ("prepend", 7000, 8000), ("prepend", 0, 8000)])


if __name__ == "__main__":
    unittest.main()
