# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-30: bridge lock and takeover, exit with iTerm2, poll timeout. Runs without iTerm2:
fake bridges are small scripts named fb_bridge.py, a fake iTerm2 is `sleep`."""
import asyncio
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

if sys.platform != "darwin":  # procinfo reads libproc, macOS only; the CI runner is Linux
    raise unittest.SkipTest("bridge lifecycle needs macOS")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fbbridge import lifecycle  # noqa: E402
from fbbridge.procinfo import proc_start  # noqa: E402

HOLDER = """import fcntl, os, signal, sys, time
if sys.argv[2] == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
os.ftruncate(fd, 0)
if sys.argv[2] != "blank":  # "blank": caught between taking the lock and writing its pid
    os.write(fd, f"{os.getpid()}\\n".encode())
print("ready", flush=True)
time.sleep(0.2 if sys.argv[2] in ("brief", "blank") else 60)
"""


class LockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "bridge.lock"
        self.logged = []
        patcher = mock.patch.object(lifecycle, "log", self.logged.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.procs, self.fds = [], []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            p.stdout.close()
        for fd in self.fds:
            os.close(fd)
        self.dir.cleanup()

    def holder(self, name, mode="normal"):
        script = Path(self.dir.name) / name
        script.write_text(HOLDER)
        p = subprocess.Popen([sys.executable, str(script), str(self.path), mode], stdout=subprocess.PIPE, text=True)
        self.procs.append(p)
        self.assertEqual(p.stdout.readline().strip(), "ready")
        return p

    def take(self, **kw):
        fd, self.took_over = lifecycle.take_lock(self.path, **kw)
        self.fds.append(fd)
        return fd

    def test_free_lock_records_our_pid(self):
        self.take()
        self.assertFalse(self.took_over)
        self.assertEqual(self.path.read_text().strip(), str(os.getpid()))
        self.assertEqual(self.logged, [])

    def test_stale_pid_in_unlocked_file_is_ignored(self):
        self.path.write_text("1\n")
        self.take()
        self.assertEqual(self.path.read_text().strip(), str(os.getpid()))

    def test_takes_over_a_leftover_bridge(self):
        old = self.holder("fb_bridge.py")
        start = time.monotonic()
        self.take(grace=3.0)
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertEqual(old.wait(2), -signal.SIGTERM)
        self.assertEqual(self.logged, [f"took over from bridge pid {old.pid}"])
        self.assertTrue(self.took_over, "a handover: the token is kept (AC-07)")

    def test_sigkill_when_sigterm_is_ignored(self):
        old = self.holder("fb_bridge.py", "stubborn")
        self.take(grace=0.5)
        self.assertEqual(old.wait(2), -signal.SIGKILL)

    def test_holder_that_exits_before_the_signal(self):
        old = self.holder("fb_bridge.py", "brief")

        def gone(pid, sig):
            raise ProcessLookupError(pid)
        with mock.patch.object(lifecycle.os, "kill", gone):
            self.take(grace=2.0)
        old.wait(2)
        self.assertEqual(self.path.read_text().strip(), str(os.getpid()))

    def test_holder_caught_before_writing_its_pid(self):
        old = self.holder("fb_bridge.py", "blank")
        self.take(grace=2.0)
        old.wait(2)
        self.assertEqual(self.path.read_text().strip(), str(os.getpid()))

    def test_refuses_to_stop_a_process_that_is_not_a_bridge(self):
        other = self.holder("holder.py")
        with self.assertRaises(lifecycle.LockError) as e:
            self.take(grace=0.5)
        self.assertIn(f"held by pid {other.pid}", str(e.exception))
        self.assertIn("not a bridge", str(e.exception))
        self.assertIsNone(other.poll())


class WatchTest(unittest.TestCase):
    def watch(self, closed=lambda: False, iterm=None):
        reasons, done = [], threading.Event()

        def on_exit(reason):
            reasons.append(reason)
            done.set()
        lifecycle.watch(on_exit, closed, iterm, every=0.05)
        return reasons, done

    def test_exits_when_iterm_is_gone(self):
        fake = subprocess.Popen(["sleep", "30"])
        reasons, done = self.watch(iterm=(fake.pid, proc_start(fake.pid)))
        self.assertFalse(done.wait(0.3))  # quiet while iTerm2 lives
        fake.kill()
        fake.wait()
        self.assertTrue(done.wait(2))
        self.assertEqual(reasons, [f"iTerm2 pid {fake.pid} gone"])

    def test_a_reused_pid_does_not_look_alive(self):
        reasons, done = self.watch(iterm=(os.getpid(), proc_start(os.getpid()) - 100))
        self.assertTrue(done.wait(2))
        self.assertIn("gone", reasons[0])

    def test_a_failing_check_is_logged_and_watching_goes_on(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise AttributeError("no closed")
            return True
        with mock.patch.object(lifecycle, "log") as log:
            reasons, done = self.watch(closed=flaky)
            self.assertTrue(done.wait(2))
        self.assertIn("watchdog", log.call_args_list[0].args[0])
        self.assertEqual(reasons, ["iTerm2 API connection closed"])

    def test_connection_closed_reads_old_and_new_websockets(self):
        class Legacy:
            closed = True

        class New:
            class state:
                name = "CLOSED"
        self.assertTrue(lifecycle.connection_closed(None))
        self.assertTrue(lifecycle.connection_closed(Legacy()))
        self.assertTrue(lifecycle.connection_closed(New()))
        Legacy.closed = False
        self.assertFalse(lifecycle.connection_closed(Legacy()))

    def test_exits_when_the_connection_closes(self):
        closed = threading.Event()
        reasons, done = self.watch(closed=closed.is_set)
        self.assertFalse(done.wait(0.3))
        closed.set()
        self.assertTrue(done.wait(2))
        self.assertEqual(reasons, ["iTerm2 API connection closed"])


class BoundedTest(unittest.TestCase):
    def test_a_call_that_never_answers_is_abandoned(self):
        async def run():
            never = asyncio.get_running_loop().create_future()
            start = time.monotonic()
            ok = await lifecycle.bounded(never, 0.2)
            return ok, time.monotonic() - start, never.cancelled()
        ok, took, cancelled = asyncio.run(run())
        self.assertFalse(ok)
        self.assertLess(took, 1.0)
        self.assertTrue(cancelled)

    def test_a_quick_poll_passes_and_its_errors_reach_the_caller(self):
        async def fine():
            return 1

        async def broken():
            raise ConnectionError("fbd down")
        self.assertTrue(asyncio.run(lifecycle.bounded(fine(), 1)))
        with self.assertRaises(ConnectionError):
            asyncio.run(lifecycle.bounded(broken(), 1))


if __name__ == "__main__":
    unittest.main()
