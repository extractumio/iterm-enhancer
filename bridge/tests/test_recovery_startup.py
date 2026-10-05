# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Startup ordering, delayed native restoration and durable completion acknowledgement."""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge.common import UserError
from fbbridge.recovery_capture import Capture
from fbbridge.recovery_runner import Runner
from fbbridge.recovery_startup import Startup


class Clock:
    def __init__(self): self.now = 0
    async def sleep(self, seconds): self.now += seconds


class StartupTest(unittest.IsolatedAsyncioTestCase):
    def fixture(self, phase="pending", enabled=True, job_status=None):
        events = []
        plan = {"epoch": "boot:iterm", "phase": phase, "snapshot": "0" * 16}
        status = {"enabled": enabled, "job": None}
        if job_status:
            status["job"] = {"id": "0" * 16, "snapshot": plan["snapshot"], "epoch": "boot:iterm", "status": job_status}
        async def call(method, path, body=None):
            events.append(path)
            if path.endswith("startup"): return plan.copy()
            if path == "/internal/recovery": return status
            if path.endswith("begin"):
                if not status["enabled"]:
                    plan["phase"] = "done"
                    return None
                status["job"] = {"id": "0" * 16, "snapshot": plan["snapshot"], "epoch": "boot:iterm", "status": "running"}
                return status["job"]
            if path.endswith("finish"):
                if plan["phase"] == "pending" and status["job"]["status"] != "complete":
                    raise UserError("Journal not complete")
                plan["phase"] = "done"
        capture = Capture(None, None, None, None)
        capture.epoch, capture.call = "boot:iterm", AsyncMock(side_effect=call)
        async def run():
            events.append("create")
            status["job"]["status"] = "complete"
            return True
        runner = types.SimpleNamespace(task=None)
        def start(_): runner.task = asyncio.create_task(run())
        runner.start = start
        startup = Startup(capture, runner)
        async def settled(): events.append("settle")
        startup.settle = AsyncMock(side_effect=settled)
        return startup, capture, runner, events, plan, status

    async def test_fresh_or_completed_startup_unblocks_capture_without_creation(self):
        startup, capture, _, events, _, _ = self.fixture(phase="done")
        await startup.initialize()
        self.assertEqual(events, ["/internal/recovery/startup"])
        self.assertTrue(capture.ready.is_set())
        startup.settle.assert_not_awaited()

    async def test_pending_source_is_reserved_before_settle_and_creation(self):
        startup, capture, _, events, plan, _ = self.fixture()
        await startup.initialize()
        self.assertEqual(events, ["/internal/recovery/startup", "/internal/recovery", "settle",
                                  "/internal/recovery/startup/begin", "create", "/internal/recovery/startup/finish"])
        self.assertTrue(capture.ready.is_set())
        self.assertEqual(plan["phase"], "done")

    async def test_normal_relaunch_settles_native_layout_but_never_starts_recovery(self):
        startup, capture, _, events, plan, _ = self.fixture(phase="done")
        plan["skipped"] = True
        await startup.initialize()
        self.assertEqual(events, ["/internal/recovery/startup", "settle"])
        self.assertTrue(capture.ready.is_set())
        await startup.initialize()
        self.assertEqual(events.count("settle"), 1)
        self.assertNotIn("create", events)

    async def test_normal_relaunch_unstable_native_layout_cannot_capture_temporary_empty_state(self):
        startup, capture, _, events, plan, _ = self.fixture(phase="done")
        plan["skipped"] = True
        startup.settle.side_effect = UserError("Startup terminal layout did not settle")
        with self.assertRaisesRegex(UserError, "did not settle"):
            await startup.initialize()
        self.assertFalse(capture.ready.is_set())
        self.assertNotIn("create", events)

    async def test_complete_journal_after_crash_finishes_without_replaying_creation(self):
        startup, capture, _, events, _, _ = self.fixture(job_status="complete")
        await startup.initialize()
        self.assertTrue(capture.ready.is_set())
        self.assertNotIn("create", events)
        startup.settle.assert_not_awaited()

    async def test_old_or_legacy_complete_job_requires_new_process_reconciliation(self):
        for old_epoch in (None, "boot:previous-iterm"):
            startup, capture, _, events, _, status = self.fixture(job_status="complete")
            status["job"]["epoch"] = old_epoch
            await startup.initialize()
            self.assertIn("create", events)
            self.assertTrue(capture.ready.is_set())

    async def test_failed_runner_is_not_completion_and_requires_explicit_retry(self):
        startup, capture, runner, events, _, status = self.fixture()
        async def failed():
            status["job"]["status"] = "interrupted"
            return False
        runner.start = lambda _: setattr(runner, "task", asyncio.create_task(failed()))
        with self.assertRaisesRegex(UserError, "interrupted"):
            await startup.initialize()
        self.assertFalse(capture.ready.is_set())
        self.assertNotIn("/internal/recovery/startup/finish", events)
        with self.assertRaisesRegex(UserError, "use Retry"):
            await startup.initialize()
        self.assertEqual(events.count("/internal/recovery/startup/begin"), 1)
        status["job"]["status"] = "complete"  # Manual Retry finishes the same reserved job.
        await startup.initialize()
        self.assertTrue(capture.ready.is_set())

    async def test_capture_and_orphan_watcher_wait_for_startup_settling(self):
        capture = Capture(None, None, None, None)
        capture.call = AsyncMock()
        runner = Runner(capture)
        tasks = [asyncio.create_task(capture.follow()), asyncio.create_task(runner.follow())]
        try:
            await asyncio.sleep(0)
            with self.assertRaisesRegex(UserError, "Startup recovery pending"):
                await capture.save(force=True)
            capture.call.assert_not_awaited()
        finally:
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_opt_out_before_or_after_failed_attempt_unblocks_without_creation(self):
        for attempted in (False, True):
            startup, capture, _, events, _, _ = self.fixture(enabled=False)
            startup.attempted = attempted
            await startup.initialize()
            self.assertTrue(capture.ready.is_set())
            self.assertNotIn("create", events)
            startup.settle.assert_not_awaited()

    async def test_backend_failure_at_each_boundary_never_unblocks_capture(self):
        for boundary in ("startup", "begin", "finish"):
            with self.subTest(boundary=boundary):
                startup, capture, _, events, _, _ = self.fixture()
                original = capture.call.side_effect
                async def unavailable(method, path, body=None):
                    if path.endswith(boundary): raise ConnectionError("private backend unavailable")
                    return await original(method, path, body)
                capture.call.side_effect = unavailable
                with self.assertRaises(ConnectionError): await startup.initialize()
                self.assertFalse(capture.ready.is_set())
                capture.call.side_effect = original
                await startup.initialize()
                self.assertTrue(capture.ready.is_set())
                self.assertEqual(events.count("create"), 1)

    async def test_task_success_cannot_replace_durable_completion(self):
        startup, capture, runner, _, _, _ = self.fixture()
        runner.start = lambda _: setattr(runner, "task", asyncio.create_task(asyncio.sleep(0, result=True)))
        with self.assertRaisesRegex(UserError, "Journal not complete"):
            await startup.initialize()
        self.assertFalse(capture.ready.is_set())


class SettlingTest(unittest.IsolatedAsyncioTestCase):
    async def test_late_native_windows_reset_the_stability_window(self):
        startup = Startup(None, None)
        clock = Clock()
        observed = []
        async def inventory():
            observed.append(clock.now)
            return ["early"] if clock.now < 2 else ["early", "native-restored"]
        startup.inventory = inventory
        with patch("fbbridge.recovery_startup.time.monotonic", side_effect=lambda: clock.now), \
             patch("fbbridge.recovery_startup.asyncio.sleep", side_effect=clock.sleep):
            await startup.settle()
        self.assertEqual(clock.now, 5)
        self.assertGreater(len(observed), 5)

    async def test_unstable_native_layout_is_bounded_and_never_starts_recovery(self):
        startup = Startup(None, None)
        clock = Clock()
        startup.inventory = AsyncMock(side_effect=lambda: [clock.now])
        with patch("fbbridge.recovery_startup.time.monotonic", side_effect=lambda: clock.now), \
             patch("fbbridge.recovery_startup.asyncio.sleep", side_effect=clock.sleep):
            with self.assertRaisesRegex(UserError, "did not settle"):
                await startup.settle()
        self.assertEqual(clock.now, 30)

    async def test_hung_inventory_and_partial_native_response_are_not_stable_empty(self):
        startup = Startup(types.SimpleNamespace(conn=None), None)
        startup.inventory = AsyncMock(side_effect=TimeoutError)
        with self.assertRaises(TimeoutError): await startup.settle()
        del startup.inventory
        fake = types.SimpleNamespace(list_sessions_response=types.SimpleNamespace(windows=["bad"]))
        with patch("fbbridge.recovery_startup.iterm2.rpc", types.SimpleNamespace(async_list_sessions=AsyncMock(return_value=fake)), create=True), \
             patch("fbbridge.recovery_startup.iterm2.Window", types.SimpleNamespace(create_from_proto=lambda *_: None), create=True):
            with self.assertRaisesRegex(UserError, "inventory incomplete"):
                await startup.inventory()
