# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Missing resources, ended retry identities and interrupted restoration reports."""
import asyncio
import copy
import os
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge.common import UserError
from fbbridge.recovery_native import NativeRestore
from fbbridge.recovery_recipes import launch_profile, local_shell, remote_bootstrap
from fbbridge.recovery_runner import Runner
from fbbridge.recovery_tmux import TmuxRestore


class MissingDirectoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_file_symlink_permission_and_profile_fall_back_with_report(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            file = folder / "file"; file.write_text("fixture")
            broken = folder / "broken"; broken.symlink_to(folder / "absent")
            denied = folder / "denied"; denied.mkdir()
            pane = {"id": "pane", "profile": "deleted-profile", "cwd_status": "known", "job": "coding-agent", "connection": {"kind": "shell"}}
            runner = types.SimpleNamespace(app=None, conn=None)
            native = NativeRestore(runner, None)
            for directory in (None, str(folder / "deleted"), str(file), str(broken), str(denied)):
                with self.subTest(directory=directory), \
                     patch("fbbridge.recovery_native.os.access", side_effect=lambda path, _: path != str(denied)):
                    command, cwd, message = await native.recipe({**pane, "cwd": directory})
                self.assertIsNone(cwd)
                self.assertIn("directory unavailable", message)
                self.assertIn("profile unavailable", message)
                self.assertIn("application was not restarted", message)
                self.assertNotIn("coding-agent", command)

    async def test_unknown_remote_directory_is_never_interpreted_as_a_local_path(self):
        native = NativeRestore(types.SimpleNamespace(app=None, conn=None), None)
        pane = {"id": "pane", "profile": None, "cwd_status": "stale", "cwd": "/srv/old", "job": "ssh",
                "connection": {"kind": "ssh", "args": ["devbox.example"]}}
        command, cwd, message = await native.recipe(pane)
        self.assertIsNone(cwd)
        self.assertNotIn("/srv/old", command)
        self.assertIn("stale or unknown", message)
        pane["connection"] = {"kind": "unsupported", "reason": "Unsupported connection"}
        command, cwd, message = await native.recipe(pane)
        self.assertIsNone(cwd)
        self.assertNotIn("/srv/old", command)
        self.assertIn("Unsupported connection", message)

    async def test_local_directory_disappears_between_preflight_and_actual_shell_start(self):
        with tempfile.TemporaryDirectory() as root:
            home, directory = Path(root) / "home", Path(root) / "cwd ' ;$(literal)"
            home.mkdir(); directory.mkdir()
            command = local_shell("/bin/pwd", str(directory))
            directory.rmdir()
            result = subprocess.run(shlex.split(command), env={"HOME": str(home)}, capture_output=True, text=True, check=True)
            self.assertEqual(Path(result.stdout.strip()).resolve(), home.resolve())
            self.assertIn("recorded directory unavailable", result.stderr)
            self.assertFalse(directory.exists())

    async def test_remote_missing_directory_keeps_shell_and_missing_home_uses_root(self):
        with tempfile.TemporaryDirectory() as root:
            missing = str(Path(root) / "remote-gone")
            # Exercise the exact generated cd/error/fallback script without personal login files.
            command = remote_bootstrap(missing).replace("exec /bin/sh -l", "exec /bin/pwd")
            result = subprocess.run(["/bin/sh", "-c", command], env={"HOME": str(Path(root) / "home-gone")},
                                    capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), "/")
            self.assertIn("recorded directory unavailable", result.stderr)

    async def test_local_tmux_missing_directory_retains_shell_and_reports_deviation(self):
        runner = types.SimpleNamespace(job={"id": "0" * 16, "steps": {}}, step=AsyncMock())
        restore = TmuxRestore(runner, {"servers": []})
        with tempfile.TemporaryDirectory() as root:
            home = Path(root) / "home"; home.mkdir()
            with patch("fbbridge.recovery_tmux.os.path.expanduser", return_value=str(home)):
                self.assertEqual(restore.directory({"id": "%1", "cwd": str(Path(root) / "gone")}), str(home.resolve()))
            self.assertIn("%1", restore.deviations)

    async def test_partial_native_fallback_is_mutable_only_when_owned_idle_and_expected(self):
        with tempfile.TemporaryDirectory() as root:
            pane = {"id": "pane", "cwd": str(Path(root) / "gone"), "connection": {"kind": "shell"}}
            runner = types.SimpleNamespace(app=None, conn=None, job={"id": "0" * 16, "steps": {}}, step=AsyncMock())
            native = NativeRestore(runner, None)
            s = types.SimpleNamespace(session_id="replacement", async_get_variable=AsyncMock(return_value=native.marker("pane")))
            with patch("fbbridge.recovery_native.fallback_directory", return_value=root), \
                 patch("fbbridge.recovery_native.resolve", AsyncMock(return_value={"cwd": root, "busy": False})):
                self.assertTrue(await native.inspect(pane, s))
                s.async_get_variable.return_value = "Default"
                self.assertFalse(await native.inspect(pane, s), "an unrelated native identity never grants fallback mutation")
            s.async_get_variable.return_value = native.marker("pane")
            for state in ({"cwd": root, "busy": True}, {"cwd": root + "/user-change", "busy": False}):
                with patch("fbbridge.recovery_native.fallback_directory", return_value=root), \
                     patch("fbbridge.recovery_native.resolve", AsyncMock(return_value=state)):
                    self.assertFalse(await native.inspect(pane, s))

    async def test_native_launch_can_start_when_home_is_inaccessible(self):
        values = {}
        profile = types.SimpleNamespace(_simple_set=lambda key, value: values.update({key: value}))
        with patch("fbbridge.recovery_recipes.iterm2.LocalWriteOnlyProfile", return_value=profile, create=True), \
             patch("fbbridge.recovery_recipes.os.path.expanduser", return_value="/unavailable/home"), \
             patch("fbbridge.recovery_recipes.directory_available", return_value=False):
            launch_profile("owned", "/bin/sh")
        self.assertEqual(values["Working Directory"], "/")


class EndedIdentityTest(unittest.IsolatedAsyncioTestCase):
    async def test_late_liveness_proof_releases_a_previously_uncertain_owned_pane(self):
        with tempfile.TemporaryDirectory() as root:
            pane = {"id": "old", "cwd": root, "connection": {"kind": "shell"}}
            runner = types.SimpleNamespace(app=None, conn=None, snapshot={"windows": [{"tabs": [{"panes": [pane]}]}]},
                                          job={"id": "0" * 16, "steps": {}}, step=AsyncMock())
            native = NativeRestore(runner, None)
            s = types.SimpleNamespace(session_id="replacement", async_get_variable=AsyncMock(return_value=native.marker("old")))
            native.app = types.SimpleNamespace(async_refresh=AsyncMock(),
                terminal_windows=[types.SimpleNamespace(tabs=[types.SimpleNamespace(all_sessions=[s])])])
            with patch("fbbridge.recovery_native.session_state", AsyncMock(side_effect=["unknown", "live"])), \
                 patch("fbbridge.recovery_native.resolve", AsyncMock(return_value={"cwd": root, "busy": False})) as resolver:
                await native.discover(profiles=False)
                self.assertFalse(await native.inspect(pane, s))
                resolver.assert_not_awaited()
                await native.discover(profiles=False)
                self.assertTrue(await native.inspect(pane, s))

    async def test_second_reboot_ended_original_marker_and_journal_are_not_adopted(self):
        for identity in ("original", "marker", "journal"):
            runner = types.SimpleNamespace(app=None, conn=None, job={"id": "0" * 16, "steps": {}},
                                          snapshot={"windows": [{"tabs": [{"panes": [{"id": "old"}]}]}]}, step=AsyncMock())
            native = NativeRestore(runner, None)
            values = {"profileName": native.marker("old") if identity == "marker" else "Default", "tmuxRole": None}
            async def variable(key): return values.get(key)
            session = types.SimpleNamespace(session_id="old" if identity == "original" else "replacement", async_get_variable=variable)
            if identity == "journal": runner.job["steps"]["old"] = {"session": session.session_id}
            runner.app = native.app = types.SimpleNamespace(async_refresh=AsyncMock(),
                terminal_windows=[types.SimpleNamespace(tabs=[types.SimpleNamespace(all_sessions=[session])])])
            with self.subTest(identity=identity), patch("fbbridge.recovery_native.session_state", AsyncMock(return_value="ended")):
                await native.discover(profiles=False)
            self.assertEqual(native.mapping, {})
            runner.step.assert_awaited_once()
            self.assertIn("Ended native pane preserved", runner.step.call_args.args[2])

    async def test_control_client_without_local_process_is_still_adopted(self):
        runner = types.SimpleNamespace(conn=None, job={"id": "0" * 16, "steps": {}},
            snapshot={"windows": [{"tabs": [{"panes": [{"id": "control"}]}]}]}, step=AsyncMock())
        session = types.SimpleNamespace(session_id="control", async_get_variable=AsyncMock(side_effect=lambda k: "client" if k == "tmuxRole" else "Default"))
        runner.app = types.SimpleNamespace(async_refresh=AsyncMock(),
            terminal_windows=[types.SimpleNamespace(tabs=[types.SimpleNamespace(all_sessions=[session])])])
        native = NativeRestore(runner, None)
        with patch("fbbridge.recovery_native.session_state", AsyncMock(return_value="live")):
            await native.discover(profiles=False)
        self.assertEqual(native.mapping, {"control": "control"})


class RunnerFailureTest(unittest.IsolatedAsyncioTestCase):
    def capture(self):
        capture = types.SimpleNamespace(conn=None, app=None, lock=asyncio.Lock(), restoring=False)
        job = {"id": "0" * 16, "snapshot": "0" * 16, "status": "running", "steps": {}}
        snapshot = {"windows": [], "servers": [], "warnings": []}
        async def call(method, path, body=None):
            if path == "/internal/recovery": return {"job": copy.deepcopy(job)}
            if "/snapshot/" in path: return snapshot
            if path.endswith("job"): job.update(copy.deepcopy(body))
        capture.call = AsyncMock(side_effect=call)
        return capture, job, snapshot

    async def test_backend_dies_on_progress_write_and_lock_is_released(self):
        capture, job, _ = self.capture()
        original = capture.call.side_effect
        async def outage(method, path, body=None):
            if path.endswith("job"): raise ConnectionError("private backend stopped")
            return await original(method, path, body)
        capture.call.side_effect = outage
        native = types.SimpleNamespace(discover=AsyncMock())
        with patch("fbbridge.recovery_runner.NativeRestore", return_value=native), patch("fbbridge.recovery_runner.log"):
            self.assertFalse(await Runner(capture).run(job["id"]))
        self.assertEqual(job["status"], "running", "failed acknowledgement cannot fabricate durable completion")
        self.assertFalse(capture.restoring)
        self.assertFalse(capture.lock.locked())

    async def test_one_failed_tab_does_not_prevent_the_next_tab_and_failure_is_durable(self):
        capture, job, snapshot = self.capture()
        snapshot["windows"] = [{"id": "window", "tabs": [{"panes": [{"id": "first"}]}, {"panes": [{"id": "second"}]}]}]
        runner = Runner(capture)
        async def tab(saved, target):
            if saved["panes"][0]["id"] == "first": raise UserError("Connection unavailable")
            await runner.step("second", "restored", "Controlled shell created")
            return types.SimpleNamespace(window=object()), False
        native = types.SimpleNamespace(discover=AsyncMock(), tab=AsyncMock(side_effect=tab), session=lambda _: None)
        with patch("fbbridge.recovery_runner.NativeRestore", return_value=native):
            self.assertTrue(await runner.run(job["id"]))
        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["steps"]["first"]["state"], "failed")
        self.assertEqual(job["steps"]["second"]["state"], "restored")

    async def test_ambiguous_native_identity_interrupts_before_any_creation(self):
        capture, job, _ = self.capture()
        native = types.SimpleNamespace(discover=AsyncMock(side_effect=UserError("Duplicate identity")), tab=AsyncMock())
        with patch("fbbridge.recovery_runner.NativeRestore", return_value=native), patch("fbbridge.recovery_runner.log"):
            self.assertFalse(await Runner(capture).run(job["id"]))
        native.tab.assert_not_awaited()
        self.assertEqual(job["status"], "interrupted")

    async def test_unavailable_display_position_reports_deviation_even_when_size_matches(self):
        capture, job, snapshot = self.capture()
        frame = {"origin": {"x": 0, "y": 0}, "size": {"width": 900, "height": 650}}
        snapshot["windows"] = [{"id": "window", "frame": frame, "fullscreen": False, "active": "old-tab",
                                "tabs": [{"id": "old-tab", "panes": []}]}]
        point = lambda x, y: types.SimpleNamespace(x=x, y=y)
        size = lambda width, height: types.SimpleNamespace(width=width, height=height)
        actual = types.SimpleNamespace(origin=point(100, 0), size=size(900, 650))
        w = types.SimpleNamespace(window_id="new-window", async_set_tabs=AsyncMock(), async_set_frame=AsyncMock(),
                                 async_get_frame=AsyncMock(return_value=actual), async_set_fullscreen=AsyncMock())
        t = types.SimpleNamespace(tab_id="new-tab", window=w, async_activate=AsyncMock())
        w.tabs = [t]
        capture.app = types.SimpleNamespace(async_refresh=AsyncMock(), get_window_by_id=lambda _: w, get_tab_by_id=lambda _: t)
        native = types.SimpleNamespace(discover=AsyncMock(), tab=AsyncMock(return_value=(t, True)))
        util = types.SimpleNamespace(Frame=lambda origin, size: types.SimpleNamespace(origin=origin, size=size), Point=point, Size=size)
        with patch("fbbridge.recovery_runner.NativeRestore", return_value=native), \
             patch("fbbridge.recovery_runner.iterm2.util", util, create=True):
            self.assertTrue(await Runner(capture).run(job["id"]))
        self.assertEqual(job["steps"]["window:window"]["state"], "deviation")
        self.assertIn("Window frame adjusted", job["steps"]["window:window"]["message"])
        w.async_set_fullscreen.assert_awaited_once_with(False)
        t.async_activate.assert_awaited_once()
