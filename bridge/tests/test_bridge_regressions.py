# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Bridge review regressions, using fake sessions, isolated records and a local fake ssh."""
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import agentctl, app, hosts, remote, windows  # noqa: E402
from fbbridge.common import UserError  # noqa: E402


class CommandsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = types.SimpleNamespace(async_send_text=mock.AsyncMock(), async_activate=mock.AsyncMock())
        self.app = types.SimpleNamespace(get_session_by_id=lambda sid: self.session if sid == "s1" else None)
        self.command = {"session": "s1", "key": "tmux:devbox:/tmp/tmux/default:%1", "job": "zsh", "intent": "cd", "text": "cd '/tmp/my dir'"}
        self.state = {"key": self.command["key"], "job": "zsh", "mode": "tmux", "busy": False}

    async def send(self, **change):
        with mock.patch.object(app, "resolve", mock.AsyncMock(return_value={**self.state, **change})):
            await app.send_command(None, self.app, self.command)

    async def test_same_session_new_tmux_pane_receives_nothing(self):
        with self.assertRaisesRegex(UserError, "pane changed"):
            await self.send(key="tmux:devbox:/tmp/tmux/default:%2")
        self.session.async_send_text.assert_not_awaited()

    async def test_new_job_and_busy_cd_receive_nothing(self):
        for change in ({"job": "fish"}, {"busy": True}):
            with self.subTest(change=change), self.assertRaises(UserError):
                await self.send(**change)
        self.session.async_send_text.assert_not_awaited()

    async def test_valid_local_and_remote_idle_cd(self):
        await self.send()
        await self.send(mode="remote", busy=True, idle=True)
        self.assertEqual(self.session.async_send_text.await_count, 2)

    async def test_remote_busy_cd_and_unknown_intent_are_refused(self):
        with self.assertRaisesRegex(UserError, "busy"):
            await self.send(mode="remote", busy=True, idle=False)
        self.command["intent"] = "execute"
        with self.assertRaisesRegex(UserError, "Unknown"):
            await self.send()
        self.session.async_send_text.assert_not_awaited()

    async def test_missing_key_job_and_session_are_refused(self):
        original = self.command.copy()
        for field in ("key", "job", "session"):
            self.command = {k: v for k, v in original.items() if k != field}
            with self.subTest(field=field), self.assertRaises(UserError):
                await self.send()
        self.session.async_send_text.assert_not_awaited()

    async def test_insert_can_target_unchanged_busy_job(self):
        self.command.update(intent="insert", job="vim", text="src/main.rs ")
        await self.send(job="vim", busy=True)
        self.session.async_send_text.assert_awaited_once_with("src/main.rs ", suppress_broadcast=True)


class RemoteSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        fake = self.root / "ssh"
        fake.write_text('#!/bin/sh\nfor last; do :; done\nHOME="$FAKE_HOME" exec sh -c "$last"\n')
        fake.chmod(0o755)
        for patcher in (
            mock.patch.object(agentctl, "RECORD", self.root / "app/agents.json"),
            mock.patch.object(agentctl, "APP_DIR", self.root / "app"),
            mock.patch.object(agentctl, "SSH", str(fake)),
            mock.patch.object(hosts, "BUILD_DIR", self.root / "build"),
            mock.patch.object(remote, "log", lambda message: None),
            mock.patch.dict(os.environ, {"FAKE_HOME": str(self.home)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        agentctl._cache.update(mtime=None, record={})
        self.addCleanup(lambda: agentctl._cache.update(mtime=None, record={}))

    def join(self, thread):
        thread.join(5)
        self.assertFalse(thread.is_alive(), "worker must finish")

    def test_ssh_timeout_is_an_agent_error(self):
        with mock.patch.object(agentctl.subprocess, "run", side_effect=subprocess.TimeoutExpired("ssh", 0.1)), \
                self.assertRaisesRegex(agentctl.AgentError, "timed out"):
            agentctl.ssh(["devbox.example"], "true", timeout=0.1)

    def test_tunnel_timeout_is_bounded_when_a_descendant_holds_both_pipes(self):
        pidfile = self.root / "descendant.pid"
        fake = self.root / "inherited-ssh"
        fake.write_text(
            f"#!{sys.executable}\nimport os,subprocess,sys,time\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'], stdin=subprocess.DEVNULL)\n"
            f"with open({str(pidfile)!r},'w') as record: record.write(str(child.pid))\n"
            "os.write(2,b'connection failed with inherited pipes\\n')\n"
            "time.sleep(30)\n")
        fake.chmod(0o755)
        tunnel = agentctl.Tunnel(["devbox.example"], self.root / "isolated.sock")
        errors = []

        def start():
            try:
                tunnel.start(timeout=0.2)
            except agentctl.AgentError as error:
                errors.append(str(error))
        with mock.patch.object(agentctl, "SSH", str(fake)):
            worker = threading.Thread(target=start)
            worker.start()
            deadline = time.monotonic() + 3
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            try:
                self.assertTrue(pidfile.exists(), "the fake descendant has inherited the pipes")
                worker.join(1.5)
                self.assertFalse(worker.is_alive(), "timeout must not wait for inherited pipe EOF")
                self.assertIn("connection failed with inherited pipes", errors[0])
                self.assertFalse(tunnel.reader.is_alive())
                self.assertTrue(tunnel.proc.stdout.closed)
                self.assertTrue(tunnel.proc.stderr.closed)
            finally:
                if pidfile.exists():
                    try:
                        os.kill(int(pidfile.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                self.join(worker)

    def test_untrusted_platform_is_refused_before_a_path_is_examined(self):
        for platform in ("/tmp/another-host-file", "../../secret", "linux-riscv64"):
            with self.subTest(platform=platform), \
                    mock.patch.object(Path, "is_file", side_effect=AssertionError("must not inspect a path")), \
                    self.assertRaises(agentctl.AgentError):
                hosts.binary_for(platform)

    def test_untrusted_tunnel_platform_cannot_choose_a_local_file(self):
        tunnel = mock.Mock()
        tunnel.start.return_value = ("old", "devbox", "alex", "../../secret")
        with mock.patch.object(agentctl, "Tunnel", return_value=tunnel), \
                mock.patch.object(hosts, "LOCAL_AGENT_ID", "aaaaaaaaaaaa"), \
                mock.patch.object(agentctl, "install") as install, \
                mock.patch.object(Path, "is_file", side_effect=AssertionError("must not inspect a path")), \
                self.assertRaises(agentctl.AgentError):
            remote.Remotes(lambda *args: None)._connect("devbox", ["devbox.example"])
        install.assert_not_called()
        tunnel.stop.assert_called_once()

    def test_record_transactions_preserve_concurrent_thread_changes(self):
        def write(prefix):
            for i in range(25):
                def change(record):
                    time.sleep(0.001)
                    record[f"{prefix}{i}"] = {"ssh": [prefix]}
                agentctl.update(change)
        threads = [threading.Thread(target=write, args=(prefix,)) for prefix in ("first", "second")]
        for thread in threads:
            thread.start()
        for thread in threads:
            self.join(thread)
        self.assertEqual(len(agentctl.load()), 50)

    def test_record_transactions_preserve_concurrent_process_changes(self):
        code = ("import sys,time; from fbbridge import agentctl; "
                "prefix=sys.argv[1]\n"
                "for i in range(20):\n"
                " def change(record):\n"
                "  time.sleep(.002)\n"
                "  record[f'{prefix}{i}']={'ssh':[prefix]}\n"
                " agentctl.update(change)\n")
        env = {**os.environ, "FB_APP_DIR": str(agentctl.RECORD.parent), "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        processes = [subprocess.Popen([sys.executable, "-c", code, prefix], env=env) for prefix in ("first", "second")]
        for process in processes:
            self.assertEqual(process.wait(10), 0)
        self.assertEqual(len(agentctl.load()), 40)

    def test_concurrent_installs_publish_distinct_complete_versions(self):
        ids = ("aaaaaaaaaaaa", "bbbbbbbbbbbb")
        binaries = []
        for aid in ids:
            binary = self.root / aid
            binary.write_text(f"#!/bin/sh\necho {aid}\n")
            binaries.append(binary)
        original_ssh = agentctl.ssh
        errors = []

        def synchronized_ssh(target, command, stdin=None, timeout=60):
            aid = next(aid for aid in ids if aid.encode() in stdin)
            other = next(other for other in ids if other != aid)
            gate = (f'touch "$FAKE_HOME/reached-{aid}"; '
                    f'while [ ! -f "$FAKE_HOME/reached-{other}" ]; do sleep 0.01; done; ')
            command = re.sub(r'(cat > [^;]+; )', lambda match: match[0] + gate, command, count=1)
            return original_ssh(target, command, stdin=stdin, timeout=timeout)

        def install(binary, aid):
            try:
                agentctl.install(["devbox.example"], binary, aid)
            except Exception as error:
                errors.append(error)
        with mock.patch.object(agentctl, "ssh", synchronized_ssh):
            threads = [threading.Thread(target=install, args=pair) for pair in zip(binaries, ids)]
            for thread in threads:
                thread.start()
            for thread in threads:
                self.join(thread)
        self.assertEqual(errors, [])
        bindir = self.home / agentctl.HOME_DIR / "bin"
        for binary, aid in zip(binaries, ids):
            self.assertEqual((bindir / f"fbd-agent-{aid}").read_bytes(), binary.read_bytes())
        self.assertEqual(list(bindir.glob(".fbd-agent.*")), [], "staging files are cleaned")

    def test_failed_install_leaves_current_helper_and_no_staging_files(self):
        binary = self.root / "binary"
        binary.write_text("#!/bin/sh\necho aaaaaaaaaaaa\n")
        agentctl.install(["devbox.example"], binary, "aaaaaaaaaaaa")
        with self.assertRaises(agentctl.AgentError):
            agentctl.install(["devbox.example"], binary, "bbbbbbbbbbbb")
        bindir = self.home / agentctl.HOME_DIR / "bin"
        self.assertEqual(os.readlink(bindir / "fbd-agent"), "fbd-agent-aaaaaaaaaaaa")
        self.assertEqual(list(bindir.glob(".fbd-agent.*")), [])

    def test_remove_during_enable_does_not_restore_the_record(self):
        reached, release = threading.Event(), threading.Event()
        remotes = remote.Remotes(lambda *args: None)
        remotes.status("devbox.example", ["devbox.example"], "devbox")

        def trial(target):
            reached.set()
            self.assertTrue(release.wait(5))
            return ("aaaaaaaaaaaa", "devbox", "alex", "linux-x86_64")
        with mock.patch.object(hosts, "LOCAL_AGENT_ID", "aaaaaaaaaaaa"), \
                mock.patch.object(agentctl, "probe", return_value=("devbox", "alex", "linux-x86_64")), \
                mock.patch.object(agentctl, "install"), \
                mock.patch.object(hosts, "trial", trial), \
                mock.patch.object(Path, "is_file", return_value=True), \
                mock.patch.object(agentctl, "remove"):
            remotes.enable("devbox.example")
            self.assertTrue(reached.wait(5))
            remotes.remove("devbox.example")
            release.set()
            for thread in threading.enumerate():
                if thread.name in ("enable-devbox.example", "remove-devbox.example"):
                    self.join(thread)
        self.assertIsNone(agentctl.entry("devbox.example"))
        self.assertNotIn("devbox.example", remotes.hosts)

    def test_remove_during_publication_never_resurrects_host(self):
        agentctl.save({"devbox": {"ssh": ["devbox.example"]}})
        reached, release = threading.Event(), threading.Event()
        posted, stopped = [], []

        def post(path, body):
            if body.get("socket"):
                reached.set()
                self.assertTrue(release.wait(5))
            posted.append((path, body))
        tunnel = types.SimpleNamespace(local=Path("/test.sock"), token="test", stop=lambda: stopped.append(True))
        remotes = remote.Remotes(post)
        remotes._set("devbox", status="connecting")
        owner = remotes.hosts["devbox"]
        with mock.patch.object(remotes, "_connect", return_value=tunnel), mock.patch.object(agentctl, "remove"):
            worker = threading.Thread(target=remotes._run, args=("devbox", ["devbox.example"], owner))
            worker.start()
            self.assertTrue(reached.wait(5))
            remotes.remove("devbox")
            release.set()
            self.join(worker)
            for thread in threading.enumerate():
                if thread.name == "remove-devbox":
                    self.join(thread)
        self.assertNotIn("devbox", remotes.hosts)
        self.assertIsNone(agentctl.entry("devbox"))
        self.assertTrue(stopped)
        self.assertIsNone(posted[-1][1]["socket"])

    def test_backend_unavailable_does_not_prevent_removing_the_helper(self):
        agentctl.save({"devbox": {"ssh": ["devbox.example"]}})
        remotes = remote.Remotes(mock.Mock(side_effect=ConnectionError("backend restarting")))
        tunnel = mock.Mock()
        remotes._set("devbox", status="up", tunnel=tunnel)
        with mock.patch.object(agentctl, "remove") as remove:
            remotes.remove("devbox")
            for thread in threading.enumerate():
                if thread.name == "remove-devbox":
                    self.join(thread)
        tunnel.stop.assert_called_once()
        remove.assert_called_once_with(["devbox.example"])
        self.assertIsNone(agentctl.entry("devbox"))

    def test_old_connection_exit_does_not_unregister_a_replacement(self):
        agentctl.save({"devbox": {"ssh": ["devbox.example"]}})
        waiting, release = threading.Event(), threading.Event()
        posted = []
        remotes = remote.Remotes(lambda path, body: posted.append(body))
        remotes._set("devbox", status="connecting")
        owner = remotes.hosts["devbox"]

        def wait():
            waiting.set()
            self.assertTrue(release.wait(5))
        old = types.SimpleNamespace(local=Path("/old.sock"), token="test", proc=types.SimpleNamespace(wait=wait), stop=mock.Mock())
        with mock.patch.object(remotes, "_connect", return_value=old):
            worker = threading.Thread(target=remotes._run, args=("devbox", ["devbox.example"], owner))
            worker.start()
            self.assertTrue(waiting.wait(5))
            with remotes.lock:
                remotes.hosts["devbox"] = {"status": "up", "tunnel": object()}
            release.set()
            self.join(worker)
        self.assertEqual(len(posted), 1, "the old worker never unregisters the replacement")
        self.assertEqual(remotes.hosts["devbox"]["status"], "up")


class AdoptViewerTest(unittest.IsolatedAsyncioTestCase):
    async def test_remote_viewer_reused_and_local_files_refused_after_restart(self):
        profile = types.SimpleNamespace(all_properties={"Custom Command": "Browser", "Initial URL": "http://127.0.0.1:47821/?view=%2Ftmp%2Ffile&host=-p%202222%20devbox.example"})
        session = types.SimpleNamespace(session_id="viewer", async_get_variable=mock.AsyncMock(return_value=windows.VIEWER_PROFILE), async_get_profile=mock.AsyncMock(return_value=profile))
        win = types.SimpleNamespace(window_id="window", current_tab=types.SimpleNamespace(current_session=session), async_activate=mock.AsyncMock())
        application = types.SimpleNamespace(terminal_windows=[win], get_window_by_id=lambda wid: win)
        backend = types.SimpleNamespace(post=mock.Mock())
        with mock.patch.object(windows.Windows, "_load", return_value={}), mock.patch.object(windows, "iterm_uptime", return_value=60):
            managed = windows.Windows(application, backend)
        await managed.adopt_viewer()
        self.assertEqual(managed.viewer_host, "-p 2222 devbox.example")
        await managed.open_viewer(None, "/another/file", "code", managed.viewer_host)
        backend.post.assert_called_once_with("/internal/viewer-open", {"path": "/another/file", "host": managed.viewer_host})
        with self.assertRaisesRegex(UserError, "close it"):
            await managed.open_viewer(None, "/local/file", "code")


if __name__ == "__main__":
    unittest.main()
