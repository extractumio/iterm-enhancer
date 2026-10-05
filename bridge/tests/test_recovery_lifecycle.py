# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Ordinary completion, GUI close, Undo and uncertain application-loss paths."""
import asyncio
import select
import signal
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge.recovery_exit import ExitWatch, NOTE_EXITSTATUS, root_identity
from fbbridge.recovery_lifecycle import Lifecycle
from fbbridge.recovery_native import NativeRestore
from fbbridge.recovery_capture import Capture
from fbbridge.recovery_recipes import launch_profile
from fbbridge.common import UserError


def session(sid="pane", marker="Default"):
    return types.SimpleNamespace(session_id=sid, async_get_variable=AsyncMock(return_value=marker))


class ClosureTest(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        capture = types.SimpleNamespace(epoch="boot:50:100", conn=types.SimpleNamespace(websocket=types.SimpleNamespace(closed=False)),
                                        call=AsyncMock(), windows=types.SimpleNamespace(viewer_id=None), window_ids=None, restoring=True)
        watch = types.SimpleNamespace(drain=lambda: [], enroll=AsyncMock(return_value=True), remove=lambda _: None, close=lambda: None)
        observer = Lifecycle(capture, watch)
        observer.process = (50, 100)
        observer.inventory = AsyncMock(return_value={"pane": session()})
        return observer, capture, watch

    async def poll(self, observer, now, identity=(42, 1, 123)):
        with patch("fbbridge.recovery_lifecycle.time.monotonic", return_value=now), \
             patch("fbbridge.recovery_lifecycle.procinfo.proc_start", return_value=100), \
             patch("fbbridge.recovery_lifecycle.root_identity", AsyncMock(return_value=identity)):
            await observer.poll()

    def events(self, capture):
        return [c.args[2] for c in capture.call.call_args_list if c.args[1].endswith("/lifecycle")]

    async def test_observed_normal_exit_retires_even_while_restore_is_running(self):
        observer, capture, watch = self.fixture()
        await self.poll(observer, 0)
        capture.call.reset_mock()
        watch.drain = lambda: [("pane", (42, 1, 123), True)]
        await self.poll(observer, 1, None)
        watch.drain = lambda: []
        self.assertEqual(self.events(capture), [])
        await self.poll(observer, 3, None)
        self.assertEqual([e["revive"] for e in self.events(capture)], [False])
        self.assertNotIn("exit_code", self.events(capture)[0])

    async def test_gui_close_waits_for_stable_inventory_and_undo_same_root_cancels_outage_retire(self):
        observer, capture, _ = self.fixture()
        await self.poll(observer, 0)
        capture.call.reset_mock()
        observer.inventory.return_value = {}
        await self.poll(observer, 1)
        self.assertEqual(self.events(capture), [])
        capture.call.side_effect = ConnectionError("private backend stopped")
        with self.assertRaises(ConnectionError):
            await self.poll(observer, 3)
        self.assertTrue(observer.blocked("pane"))
        observer.inventory.return_value = {"pane": session()}
        capture.call.side_effect = None
        capture.call.reset_mock()
        await self.poll(observer, 4)
        self.assertEqual([e["revive"] for e in self.events(capture)], [True])
        self.assertFalse(observer.blocked("pane"))

    async def test_undo_after_committed_close_revives_and_reenrolls_root(self):
        observer, capture, _ = self.fixture()
        await self.poll(observer, 0)
        observer.inventory.return_value = {}
        await self.poll(observer, 1)
        await self.poll(observer, 3)
        self.assertNotIn("pane", observer.known)
        capture.call.reset_mock()
        observer.inventory.return_value = {"pane": session()}
        await self.poll(observer, 4, (43, 2, 124))
        self.assertEqual([e["revive"] for e in self.events(capture)], [True])

    async def test_api_failure_gap_and_iterm_shutdown_do_not_commit_closures(self):
        observer, capture, watch = self.fixture()
        await self.poll(observer, 0)
        capture.call.reset_mock()
        observer.inventory.side_effect = ConnectionError("iTerm unavailable")
        watch.drain = lambda: [("pane", (42, 1, 123), True)]
        with self.assertRaises(ConnectionError):
            await self.poll(observer, 1, None)
        self.assertEqual(self.events(capture), [])
        observer.inventory.side_effect = None
        observer.inventory.return_value = {}
        capture.conn.websocket.closed = True
        await self.poll(observer, 5, None)
        self.assertEqual(self.events(capture), [])
        self.assertEqual(observer.exits, {})
        capture.conn.websocket.closed = False
        watch.drain = lambda: []
        await self.poll(observer, 10)
        await self.poll(observer, 20)
        self.assertEqual(self.events(capture), [], "long inventory gap restarts closure grace")

    async def test_app_dies_during_inventory_or_metadata_rpc_preserves_history(self):
        for stage in ("inventory", "metadata"):
            observer, capture, watch = self.fixture()
            await self.poll(observer, 0)
            capture.call.reset_mock()
            watch.drain = lambda: [("pane", (42, 1, 123), True)]
            await self.poll(observer, 1, None)
            watch.drain = lambda: []
            async def stop_inventory():
                capture.conn.websocket.closed = True
                return {}
            async def stop_variable(_):
                capture.conn.websocket.closed = True
                return "Default"
            if stage == "inventory": observer.inventory.side_effect = stop_inventory
            else: observer.inventory.return_value["pane"].async_get_variable.side_effect = stop_variable
            await self.poll(observer, 3, None)
            self.assertEqual(self.events(capture), [])

    async def test_missing_watch_permission_is_not_ordinary_exit_evidence(self):
        observer, capture, watch = self.fixture()
        watch.enroll.return_value = False
        await self.poll(observer, 0)
        self.assertEqual(observer.identities, {"pane":(42, 1, 123)})
        capture.call.reset_mock()
        await self.poll(observer, 3, None)
        self.assertEqual(self.events(capture), [])

    async def test_undo_positive_live_root_revives_even_when_exit_status_watch_is_denied(self):
        observer, capture, watch = self.fixture()
        watch.enroll.return_value = False
        observer.known["pane"] = "Default"
        observer.absent["pane"] = 0
        observer.queue("pane", False)
        await self.poll(observer, 1)
        self.assertEqual([e["revive"] for e in self.events(capture)], [True])
        self.assertFalse(observer.blocked("pane"))

    async def test_new_live_root_cancels_pending_normal_exit_and_same_pid_keeps_new_watch(self):
        observer, capture, watch = self.fixture()
        watch.remove = unittest.mock.Mock()
        await self.poll(observer, 0)
        observer.exits["pane"] = 1
        observer.queue("pane", False)
        capture.call.reset_mock()
        await self.poll(observer, 4, (42, 2, 124))
        self.assertEqual([e["revive"] for e in self.events(capture)], [True])
        watch.remove.assert_not_called()
        self.assertEqual(observer.exits, {})

    async def test_live_tmux_control_undo_cancels_close_without_local_pid(self):
        observer, capture, _ = self.fixture()
        s = session()
        s.async_get_variable.side_effect = lambda k: "client" if k == "tmuxRole" else "Default"
        observer.inventory.return_value = {"pane": s}
        await self.poll(observer, 0, None)
        observer.inventory.return_value = {}
        await self.poll(observer, 1, None)
        capture.call.side_effect = ConnectionError
        with self.assertRaises(ConnectionError): await self.poll(observer, 3, None)
        capture.call.side_effect = None
        capture.call.reset_mock()
        observer.inventory.return_value = {"pane": s}
        await self.poll(observer, 4, None)
        self.assertEqual([e["revive"] for e in self.events(capture)], [True])

    async def test_creation_guard_checks_local_uncommitted_marker_and_durable_source(self):
        observer, capture, _ = self.fixture()
        observer.known["created"] = "File Browser Restore " + "0" * 16 + ":source"
        observer.queue("created", False)
        observer.poll = AsyncMock()
        capture.lifecycle = observer
        runner = types.SimpleNamespace(capture=capture, app=None, conn=None, job={"id":"0" * 16,"steps":{}})
        native = NativeRestore(runner, None)
        with self.assertRaisesRegex(UserError, "Intentionally terminated"):
            await native.guard_creation({"id":"source"})
        del capture.lifecycle
        capture.call.return_value = {"retired":["source"]}
        with self.assertRaisesRegex(UserError, "Intentionally terminated"):
            await native.guard_creation({"id":"source"})

    async def test_early_exit_or_close_blocks_native_and_control_mutations_before_grace(self):
        for kind in ("exits", "absent"):
            observer, capture, _ = self.fixture()
            observer.known["created"] = "Default"  # user changed the creation marker
            getattr(observer, kind)["created"] = 1
            observer.poll = AsyncMock()
            capture.lifecycle = observer
            runner = types.SimpleNamespace(capture=capture, app=None, conn=None,
                job={"id":"0" * 16,"steps":{"source":{"session":"created"}}})
            tmux = types.SimpleNamespace(connection=AsyncMock())
            native = NativeRestore(runner, tmux)
            with self.assertRaisesRegex(UserError, "Intentionally terminated"):
                await native.control({"panes":[{"id":"source"}]})
            tmux.connection.assert_not_awaited()

    async def test_failed_owned_ssh_and_tmux_launch_remain_eligible_for_retry(self):
        for kind in ("ssh", "tmux"):
            observer, capture, watch = self.fixture()
            marker = "File Browser Restore " + "0" * 16 + ":source"
            observer.remember(marker, {"id":"source", "connection":{"kind":kind}})
            observer.inventory.return_value = {"pane":session(marker=marker)}
            await self.poll(observer, 0)
            capture.call.reset_mock()
            watch.drain = lambda: [("pane", (42, 1, 123), False)]
            with patch("fbbridge.recovery_lifecycle.session_state", AsyncMock(return_value="ended")):
                await self.poll(observer, 1, None)
                watch.drain = lambda: []
                await self.poll(observer, 3, None)
            self.assertEqual(observer.failed, {"pane"})
            self.assertEqual(self.events(capture), [])
            self.assertFalse(observer.blocked("source"), "failure permits an explicit Retry")
            observer.inventory.return_value = {}
            await self.poll(observer, 4)
            await self.poll(observer, 6)
            self.assertEqual(self.events(capture)[0]["revive"], False, "explicit close still retires failed diagnostics")
            self.assertEqual(observer.failed, set())

    async def test_restart_observes_ended_controlled_connection_as_unknown_failure_not_exit(self):
        observer, capture, _ = self.fixture()
        marker = "File Browser Restore " + "0" * 16 + ":source"
        observer.remember(marker, {"id":"source", "connection":{"kind":"ssh"}})
        observer.inventory.return_value = {"pane":session(marker=marker)}
        with patch("fbbridge.recovery_lifecycle.session_state", AsyncMock(return_value="ended")):
            await self.poll(observer, 0, None)
            await self.poll(observer, 3, None)
        self.assertEqual(observer.failed, {"pane"})
        self.assertEqual(self.events(capture), [])

    async def test_successful_connection_exit_retires_and_closes_only_exact_owned_diagnostics(self):
        observer, capture, watch = self.fixture()
        marker = "File Browser Restore " + "0" * 16 + ":source"
        s = session(marker=marker)
        s.async_close = AsyncMock()
        observer.remember(marker, {"id":"source", "connection":{"kind":"ssh"}})
        observer.inventory.return_value = {"pane":s}
        await self.poll(observer, 0)
        capture.call.reset_mock()
        watch.drain = lambda: [("pane", (42, 1, 123), True)]
        with patch("fbbridge.recovery_lifecycle.session_state", AsyncMock(return_value="ended")):
            await self.poll(observer, 1, None)
            watch.drain = lambda: []
            await self.poll(observer, 3, None)
        self.assertEqual([e["revive"] for e in self.events(capture)], [False])
        s.async_close.assert_awaited_once_with(force=True)

    async def test_failed_connection_or_unconfirmed_close_cannot_overwrite_latest_snapshot(self):
        for failed, absent, live in (({"pane"}, {}, True), (set(), {"pane":1}, True), (set(), {}, False)):
            capture = Capture(None, None, None, None)
            capture.epoch = "boot:50:100"
            capture.ready.set()
            capture.snapshot = AsyncMock(return_value={"windows":[]})
            capture.call = AsyncMock()
            capture.lifecycle = types.SimpleNamespace(poll=AsyncMock(), failed=failed, absent=absent,
                exits={}, pending={}, connecting=set(), operational=lambda: live, unconfirmed=lambda: bool(absent))
            with self.assertRaises(UserError): await capture.save()
            capture.call.assert_not_awaited()

    def test_owned_connection_profiles_retain_failed_diagnostics_shell_profiles_close(self):
        for close in (True, False):
            values = {}
            profile = types.SimpleNamespace(_simple_set=lambda k, v: values.update({k:v}))
            with patch("fbbridge.recovery_recipes.iterm2.LocalWriteOnlyProfile", return_value=profile, create=True):
                launch_profile("owned", "/bin/sh", close_on_end=close)
            self.assertEqual(values["Close Sessions On End"], close)

    async def test_old_diagnostics_do_not_retire_live_replacement_even_before_new_journal_ack(self):
        for generations in (False, True):
            observer, capture, _ = self.fixture()
            old_marker = "File Browser Restore " + "0" * 16 + ":original"
            new_marker = "File Browser Restore " + "1" * 16 + ":old" if generations else old_marker
            observer.remember(old_marker, {"id":"original","connection":{"kind":"ssh"}})
            observer.remember(new_marker, {"id":"old" if generations else "original","connection":{"kind":"ssh"}})
            observer.known["old"] = old_marker
            observer.identities["old"] = (41, 1, 123)
            observer.failed.add("old")
            observer.connecting.add("old")
            observer.pending["old"] = {"epoch":capture.epoch,"session":"old","marker":old_marker,"revive":False}
            observer.inventory.return_value = {"new":session("new", new_marker)}
            await self.poll(observer, 0, (42, 2, 124))
            self.assertEqual(observer.failed, set())
            self.assertEqual(observer.connecting, set())
            self.assertFalse(observer.blocked("original", "old"))
            self.assertFalse(observer.unconfirmed())
            await self.poll(observer, 2, (42, 2, 124))
            self.assertFalse(any(not e["revive"] for e in self.events(capture)), "even an outdated old journal receives no retire")

    async def test_generic_same_profile_names_never_suppress_a_real_close(self):
        observer, capture, _ = self.fixture()
        observer.known["old"] = "Default"
        observer.inventory.return_value = {"new":session("new")}
        await self.poll(observer, 0)
        await self.poll(observer, 2)
        self.assertIn("old", {e["session"] for e in self.events(capture) if not e["revive"]})

    async def test_expired_source_marker_does_not_block_inventory_but_backend_outage_is_reported(self):
        observer, capture, _ = self.fixture()
        marker = "File Browser Restore " + "0" * 16 + ":expired"
        observer.inventory.return_value = {"pane":session(marker=marker)}
        capture.call.return_value = {"entries":[]}
        await self.poll(observer, 0)
        self.assertIn("pane", observer.identities)
        self.assertIsNone(observer.recipe("pane"))
        observer.recipes.clear()
        capture.call.side_effect = ConnectionError("private backend unavailable")
        with self.assertRaises(ConnectionError): await self.poll(observer, 1)

    async def test_auth_pending_cannot_replace_known_remote_directory_with_local_startup_directory(self):
        capture = Capture(None, None, None, None)
        capture.epoch = "boot:50:100"
        capture.ready.set()
        source = {"id":"original", "cwd":"/srv/project", "cwd_status":"known", "connection":{"kind":"ssh","args":["devbox.example"]}}
        observer = types.SimpleNamespace(recipe=lambda _:source, connecting=set(), failed=set(),
            poll=AsyncMock(), operational=lambda:True, unconfirmed=lambda:False)
        capture.lifecycle = observer
        capture.profile = AsyncMock(return_value={})
        s = session()
        s.grid_size = types.SimpleNamespace(width=80,height=24)
        with patch("fbbridge.recovery_capture.resolve", AsyncMock(return_value={"mode":"local","cwd":"/tmp/local","job":"ssh"})):
            pending = await capture.pane(s, False)
        self.assertEqual(pending["connection"], source["connection"])
        self.assertIsNone(pending["cwd"])
        self.assertEqual(observer.connecting, {"pane"})
        capture.snapshot = AsyncMock(return_value={"windows":[]})
        capture.call = AsyncMock()
        with self.assertRaisesRegex(UserError, "directory awaiting confirmation"):
            await capture.save()
        capture.call.assert_not_awaited()
        self.assertEqual(source["cwd"], "/srv/project", "old source remains the next epoch's directory target")


class ExitEvidenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_owned_process_exit_zero_and_nonzero_are_completed_signal_is_not(self):
        for code in (0, 7, None):
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.15); raise SystemExit(" + str(code or 0) + ")"])
            watch = ExitWatch()
            identity = process.pid, 1, 123
            try:
                with patch("fbbridge.recovery_exit.root_identity", AsyncMock(return_value=identity)):
                    self.assertTrue(await watch.enroll(session(), identity))
                if code is None: process.send_signal(signal.SIGTERM)
                await asyncio.to_thread(process.wait)
                results = watch.drain()
                self.assertEqual(results, [] if code is None else [("pane", identity, code == 0)])
            finally:
                if process.poll() is None: process.kill(); process.wait()
                watch.close()

    async def test_pid_reuse_and_watch_permission_error_never_register_identity(self):
        watch = ExitWatch.__new__(ExitWatch)
        watch.queue = types.SimpleNamespace(control=unittest.mock.Mock())
        watch.sessions = {}
        with patch("fbbridge.recovery_exit.root_identity", AsyncMock(return_value=(42, 99, 123))):
            self.assertFalse(await watch.enroll(session(), (42, 1, 123)))
        self.assertEqual(watch.sessions, {})
        watch.queue.control.side_effect = PermissionError
        self.assertFalse(await watch.enroll(session(), (42, 1, 123)))

    async def test_missing_status_and_signal_are_unknown_not_normal_exit(self):
        watch = ExitWatch.__new__(ExitWatch)
        for flags, data in ((select.KQ_NOTE_EXIT, 0), (NOTE_EXITSTATUS, signal.SIGKILL)):
            watch.sessions = {42:("pane", (42, 1, 123))}
            watch.queue = types.SimpleNamespace(control=lambda *_: [types.SimpleNamespace(ident=42,fflags=flags,data=data)])
            self.assertEqual(watch.drain(), [])

    async def test_invalid_missing_pid_or_tty_and_permission_failure_are_unknown(self):
        for pid in (None, True, 0, -1, "invalid"):
            s = session()
            s.async_get_variable.side_effect = lambda k: pid if k == "pid" else None
            self.assertIsNone(await root_identity(s))
        s.async_get_variable.side_effect = PermissionError
        self.assertIsNone(await root_identity(s))
