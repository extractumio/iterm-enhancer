# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Normal application exit evidence and durable-before-stop ordering, without iTerm2."""
import ast
import select
import signal
import subprocess
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if sys.platform != "darwin":
    raise unittest.SkipTest("application exit evidence needs macOS")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fbbridge.procinfo import proc_start
from fbbridge.recovery_exit import NOTE_EXITSTATUS
from fbbridge.recovery_quit import QuitWatch


class QuitWatchTest(unittest.TestCase):
    def fixture(self, flags=NOTE_EXITSTATUS, status=0):
        event = types.SimpleNamespace(ident=42, fflags=flags, data=status)
        queue = Mock()
        queue.control.side_effect = [[], [event]]
        record = Mock()
        with patch("fbbridge.recovery_quit.proc_start", return_value=100), patch("fbbridge.recovery_quit.select.kqueue", return_value=queue):
            watch = QuitWatch((42, 100), record)
        self.addCleanup(watch.close)
        return watch, queue, record

    def test_zero_status_is_delivered_once_after_kernel_proof(self):
        watch, queue, record = self.fixture()
        self.assertTrue(watch.finish())
        self.assertTrue(watch.finish())
        record.assert_called_once_with()
        self.assertEqual(queue.control.call_count, 2)

    def test_nonzero_signal_missing_status_and_foreign_pid_never_record_quit(self):
        for flags, status in ((NOTE_EXITSTATUS, 7 << 8), (NOTE_EXITSTATUS, signal.SIGKILL), (select.KQ_NOTE_EXIT, 0)):
            watch, _, record = self.fixture(flags, status)
            self.assertFalse(watch.finish())
            record.assert_not_called()
        watch, queue, record = self.fixture()
        queue.control.side_effect = None
        queue.control.return_value = [types.SimpleNamespace(ident=99, fflags=NOTE_EXITSTATUS, data=0)]
        self.assertFalse(watch.finish())
        record.assert_not_called()

    def test_backend_outage_retries_cached_evidence_without_reading_a_new_pid(self):
        watch, queue, record = self.fixture()
        record.side_effect = [ConnectionError(), None]
        with patch("fbbridge.recovery_quit.log"):
            self.assertFalse(watch.finish())
        with patch("fbbridge.recovery_quit.proc_start", return_value=999):
            self.assertTrue(watch.finish())
        self.assertEqual(queue.control.call_count, 2)
        self.assertEqual(record.call_count, 2)

    def test_live_parent_canceled_quit_or_bridge_takeover_has_no_exit_evidence(self):
        watch, queue, record = self.fixture()
        queue.control.side_effect = None
        queue.control.return_value = []
        self.assertFalse(watch.finish(timeout=0))
        self.assertFalse(watch.finish(timeout=3))
        self.assertEqual(queue.control.call_args.args, (None, 1, 3))
        record.assert_not_called()

    def test_registration_denial_pid_reuse_and_poll_failure_are_unknown(self):
        record, queue = Mock(), Mock()
        for starts in ([999], [100, 999]):
            with patch("fbbridge.recovery_quit.proc_start", side_effect=starts), patch("fbbridge.recovery_quit.select.kqueue", return_value=queue):
                watch = QuitWatch((42, 100), record)
            self.assertFalse(watch.finish())
            watch.close()
        queue.control.side_effect = PermissionError
        with patch("fbbridge.recovery_quit.proc_start", return_value=100), patch("fbbridge.recovery_quit.select.kqueue", return_value=queue), patch("fbbridge.recovery_quit.log"):
            watch = QuitWatch((42, 100), record)
        self.assertFalse(watch.finish())
        watch, queue, record = self.fixture()
        queue.control.side_effect = OSError
        self.assertFalse(watch.finish())
        record.assert_not_called()

    def test_real_owned_processes_observe_status_without_quitting_iterm(self):
        for code in (0, 7, None):
            process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.readline(); raise SystemExit(" + str(code or 0) + ")"], stdin=subprocess.PIPE)
            record = Mock()
            watch = QuitWatch((process.pid, proc_start(process.pid)), record)
            try:
                self.assertIsNotNone(watch.queue)
                if code is None:
                    process.send_signal(signal.SIGTERM)
                else:
                    process.stdin.write(b"go\n")
                    process.stdin.flush()
                process.wait(timeout=3)
                self.assertEqual(watch.finish(timeout=0.1), code == 0)
                self.assertEqual(record.call_count, int(code == 0))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                watch.close()

    def test_api_shutdown_before_process_exit_waits_for_the_event(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.15)"])
        record = Mock()
        watch = QuitWatch((process.pid, proc_start(process.pid)), record)
        try:
            self.assertTrue(watch.finish(timeout=1))
            record.assert_called_once()
        finally:
            process.wait(timeout=3)
            watch.close()

    def test_watchdog_and_takeover_serialize_marker_delivery(self):
        watch, _, record = self.fixture()
        entered, release = threading.Event(), threading.Event()
        def deliver():
            entered.set()
            self.assertTrue(release.wait(2))
        record.side_effect = deliver
        results = []
        first = threading.Thread(target=lambda: results.append(watch.finish()))
        second = threading.Thread(target=lambda: results.append(watch.finish()))
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            second.start()
        finally:
            release.set()
            first.join(3)
            if second.ident: second.join(3)
        self.assertEqual(results, [True, True])
        record.assert_called_once()

    def test_stop_drains_evidence_before_backend_even_during_fast_takeover(self):
        source = Path(__file__).resolve().parents[1] / "fbbridge/app.py"
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "stop")
        for reason, expected_timeout in (("SIGTERM", 0), ("iTerm2 API connection closed", 3), ("iTerm2 pid 42 gone", 0)):
            events = []
            namespace = {
                "log":lambda _: None,
                "_quit":types.SimpleNamespace(finish=lambda timeout: events.append(("record", timeout)), close=lambda: events.append("close")),
                "remotes":types.SimpleNamespace(stop=lambda: events.append("remotes")),
                "backend":types.SimpleNamespace(stop=lambda: events.append("backend")),
                "os":types.SimpleNamespace(_exit=lambda _: events.append("exit")),
            }
            exec(compile(ast.Module(body=[node],type_ignores=[]), str(source), "exec"), namespace)
            namespace["stop"](reason)
            self.assertEqual(events, [("record",expected_timeout), "close", "remotes", "backend", "exit"])

    def test_process_watch_does_not_read_argv_environment_or_terminal_output(self):
        watch, _, record = self.fixture()
        with patch("fbbridge.procinfo.proc_argv", side_effect=AssertionError("argv must not be read")):
            self.assertTrue(watch.finish())
        record.assert_called_once()
