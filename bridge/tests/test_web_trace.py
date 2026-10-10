# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The latency trace (AC-55): what a page may record, the file and its limits, a key's stages
joined to the frame that shows it, and the report."""
import asyncio
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge import common  # noqa: E402
from fbbridge.web import trace  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class Client:
    def __init__(self):
        self.sent, self.session, self.conn, self.hub = [], None, None, SimpleNamespace(layout=None)

    async def send(self, msg):
        self.sent.append(msg)

    async def send_quietly(self, msg):
        self.sent.append(msg)


class Tab:
    tmux_window_id = None


class Session:
    session_id = "w0t0p0:ABC"
    tab = Tab()


def lines(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines()]


class TraceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        patch = mock.patch.object(common, "LOG_DIR", Path(self.dir.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(trace.RECORDER.stop)

    def test_a_page_record_keeps_only_known_names_numbers_and_words(self):
        rec = trace.clean({"ev": "echo", "k": 3, "cls": "char", "path": "input", "total": 12.3456, "via": "text",
                           "data": "secret", "cur": "x", "rt": float("nan"), "in": "5", "woke": "rm -rf", "buf": True})
        self.assertEqual(rec, {"ev": "echo", "k": 3, "cls": "char", "path": "input", "total": 12.35, "via": "text", "buf": True})
        self.assertIsNone(trace.clean({"ev": "shell", "cmd": "ls"}))
        self.assertIsNone(trace.clean(["echo"]))
        self.assertEqual(trace.clean({"ev": "open", "sid": "w0t0p0:ABC", "first": 4}), {"ev": "open", "sid": "w0t0p0:ABC", "first": 4})
        self.assertEqual(trace.clean({"ev": "open", "sid": "../../etc/passwd x"}), {"ev": "open"})

    def test_a_key_is_joined_to_the_frame_where_the_cursor_moved(self):
        async def go():
            c = Client()
            t = trace.Tracer(c)
            await t.handle({"t": "trace", "on": True, "resume": False})
            self.assertEqual(c.sent[-1]["on"], True)
            self.assertEqual(c.sent[-1]["left"], trace.TRACE_FOR)
            c.hub.layout = [{"items": [{"id": "w0t0p0:ABC", "host": "devbox.example"}]}]
            t.opening(Session(), 0.0)
            self.assertEqual(t.mode, {"mode": "shell", "remote": True})
            key = t.key({"t": "in", "data": "a", "k": 7})
            t.step(key, "fwd")
            t.step(key, "type", via="text")
            t.typed(key)
            t.woken("wake")
            t.read(trace.time.perf_counter())
            self.assertEqual(t.frame(False), {}, "a spinner's frame answers no key")
            t.read(trace.time.perf_counter())
            out = t.frame(True)["tr"]
            b = out["ks"][0]
            self.assertEqual((b["k"], b["via"], b["polls"], b["skip"], b["woke"]), (7, "text", 1, 1, "next"))
            self.assertTrue(all(isinstance(b[n], float) for n in ("fwd", "type", "wait", "read", "bt")))
            self.assertEqual(t.frame(True), {}, "a key is answered once")
            await t.handle({"t": "trace", "ev": [{"ev": "echo", "k": 7, "total": 40, "data": "a"}] * 3 + ["junk"]})
            t.close()
            trace.RECORDER.file.flush()
            recs = lines(trace.RECORDER.path)
            self.assertEqual([r["ev"] for r in recs], ["start", "open", "key", "echo", "echo", "echo"])
            self.assertEqual(recs[3], {**recs[3], "src": "page", "k": 7, "total": 40, "mode": "shell", "remote": True})
            self.assertNotIn("data", json.dumps(recs))
            self.assertEqual(stat.S_IMODE(os.stat(trace.RECORDER.path).st_mode), 0o600)
        asyncio.run(go())

    def test_off_nothing_is_recorded_and_the_messages_carry_nothing(self):
        t = trace.Tracer(Client())
        self.assertIsNone(t.key({"t": "in", "data": "a", "k": 1}))
        t.read(0.0)
        self.assertEqual(t.frame(True), {})
        self.assertFalse(list(Path(self.dir.name).glob("trace-*")))

    def test_the_trace_ends_on_the_bridges_clock_and_a_reconnect_after_it_is_told(self):
        async def go():
            c = Client()
            t = trace.Tracer(c)
            await t.handle({"t": "trace", "on": True, "resume": False})
            self.assertTrue(trace.RECORDER.on)
            trace.RECORDER.until = 0           # 15 minutes later
            self.assertFalse(trace.RECORDER.on)
            trace.note("poll", ms=3)           # written nowhere
            again = Client()
            await trace.Tracer(again).handle({"t": "trace", "on": True, "resume": True})
            self.assertEqual(again.sent, [{"t": "trace", "on": False}])
            t.close()
        asyncio.run(go())

    def test_a_second_page_joins_the_trace_and_a_failed_write_only_ends_it(self):
        async def go():
            await trace.Tracer(Client()).handle({"t": "trace", "on": True, "resume": False})
            path = trace.RECORDER.path
            second = trace.Tracer(Client())
            await second.handle({"t": "trace", "on": True, "resume": False})
            self.assertEqual(trace.RECORDER.path, path, "joined, not restarted")
            key = second.key({"t": "in", "data": "a", "k": 1})
            second.typed(key)
            second.read(key["m"] + 0.001)
            with mock.patch.object(trace.RECORDER.file, "write", side_effect=OSError(28, "No space left on device")), \
                    mock.patch.object(trace, "log"):
                self.assertIn("tr", second.frame(True), "the frame is still sent")
            self.assertFalse(trace.RECORDER.on)
            trace.note("poll", ms=1)
            second.close()
        asyncio.run(go())

    def test_a_key_typed_during_a_read_waits_for_the_next_and_the_first_cause_counts(self):
        async def go():
            t = trace.Tracer(Client())
            await t.handle({"t": "trace", "on": True, "resume": False})
            began = trace.time.perf_counter()
            key = t.key({"t": "in", "data": "a", "k": 1})
            t.typed(key)
            t.woken("wake")
            t.woken("notify")
            t.read(began)                         # this read began before the key arrived
            self.assertEqual(t.frame(True), {})
            t.read(key["m"] + 0.001)
            b = t.frame(True)["tr"]["ks"][0]
            self.assertEqual((b["k"], b["polls"]), (1, 0))
            t.woken("wake")
            t.woken("notify")
            t.read(trace.time.perf_counter())
            self.assertEqual(t.read_woke, "wake")
            t.close()
        asyncio.run(go())

    def test_the_file_stops_at_its_size_cap(self):
        async def go():
            await trace.Tracer(Client()).handle({"t": "trace", "on": True, "resume": False})
            with mock.patch.object(trace, "MAX_BYTES", 300):
                for _ in range(20):
                    trace.note("lag", ms=25.0)
                self.assertFalse(trace.RECORDER.on)
                self.assertLess(trace.RECORDER.size, 400)
        asyncio.run(go())

    def test_the_report_places_the_time_by_stage_and_mode(self):
        spec = importlib.util.spec_from_file_location("trace_report", ROOT / "scripts" / "trace_report.py")
        report = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(report)
        echo = {"src": "page", "ev": "echo", "cls": "char", "path": "input", "mode": "tmux", "remote": False,
                "in": 1, "rt": 900, "bt": 850, "fwd": 0.5, "type": 2, "wait": 840, "read": 3, "enc": 1, "draw": 5, "paint": 10, "total": 920}
        recs = [{"at": 0, "src": "bridge", "ev": "start", "build": "b1"}, {"at": 1000, **echo},
                {"at": 2000, **echo, "mode": "shell", "total": 40, "rt": 20, "bt": 10, "wait": 5},
                {"at": 3000, "src": "bridge", "ev": "tmux", "ms": 700}, {"at": 4000, "src": "page", "ev": "ping", "rtt": 30}]
        out = io.StringIO()
        report.report(recs, out=lambda s="": out.write(s + "\n"))
        text = out.getvalue()
        self.assertIn("tmux/local: total n=1     p50    920", text)
        self.assertIn("shell/local: total n=1     p50     40", text)
        self.assertRegex(text, r"iTerm2/shell/tmux: until a read shows it\s+n=1\s+p50\s+840")
        self.assertRegex(text, r"connection \(both ways\) and queue\s+n=1\s+p50\s+50")
        self.assertIn("tmux round trip (display -p)                    n=1     p50    700", text)


if __name__ == "__main__":
    unittest.main()
