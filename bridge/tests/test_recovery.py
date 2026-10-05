# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Recovery recipes, retry identities and real private tmux graphs."""
import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import shutil
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge.recovery_recipes import capture_ssh_recipe, remote_bootstrap, ssh_command, ssh_recipe
from fbbridge.recovery_native import NativeRestore, canonical
from fbbridge.recovery_capture import Capture
from fbbridge.recovery_tmux import TmuxRestore, remap_layout
from fbbridge.recovery_tmux_capture import TmuxGraphs, local_command
from fbbridge.recovery_runner import Runner
from fbbridge.common import UserError


class RecipesTest(unittest.TestCase):
    def test_narrow_ssh_and_generated_remote_bootstrap(self):
        recipe = ssh_recipe(["/usr/bin/ssh", "-tt", "-p2222", "-J", "jump.example", "alex@devbox.example"])
        self.assertEqual(recipe, ["-p", "2222", "-J", "jump.example", "alex@devbox.example"])
        directory = "/srv/project with 'quote;$(literal)"
        command = shlex.split(ssh_command(recipe, directory))
        self.assertEqual(shlex.split(command[-1]), ["/bin/sh", "-c", remote_bootstrap(directory)])
        self.assertIn("PermitLocalCommand=no", command)
        for argv in (["ssh", "host", "app"], ["ssh", "-o", "ProxyCommand=app", "host"], ["ssh", "-p0", "host"],
                     ["autossh", "host"], ["ssh", "-J", "a;app", "host"], ["ssh", "host\napp"]):
            self.assertIsNone(ssh_recipe(argv), argv)
        with self.assertRaises(UserError):
            remote_bootstrap("relative")

    def test_projected_tree_preserves_orientation_and_order(self):
        tree = {"vertical": True, "children": [{"pane": "a"}, {"vertical": False, "children": [{"pane": "b"}, {"pane": "c"}]}]}
        self.assertEqual(canonical(tree, {"a", "c"}), {"vertical": True, "children": [{"pane": "a"}, {"pane": "c"}]})
        self.assertEqual(canonical(tree, {"c"}), {"pane": "c"})

    def test_restored_ssh_round_trips_without_retaining_generated_commands(self):
        recipe = ["-p", "2222", "-J", "jump.example", "alex@devbox.example"]
        for cwd in (None, "/srv/project", "/srv/a ' ;$(literal)"):
            argv = shlex.split(ssh_command(recipe, cwd))
            self.assertEqual(capture_ssh_recipe(argv), recipe)
            self.assertIsNone(ssh_recipe(argv), "ordinary input parser must still reject remote commands")
        legacy = shlex.split(ssh_command(recipe, command="cd -- /srv/project || exit 1; exec /bin/sh -l"))
        self.assertEqual(capture_ssh_recipe(legacy), recipe)

    def test_generated_ssh_normalization_rejects_noncanonical_or_executable_suffixes(self):
        recipe = ["devbox.example"]
        for script in ("touch /tmp/forbidden", remote_bootstrap("/srv/project") + "; touch /tmp/forbidden",
                       "if ! cd -- /srv/project; then exec app; fi; exec /bin/sh -l"):
            self.assertIsNone(capture_ssh_recipe(shlex.split(ssh_command(recipe, command=script))))
        argv = shlex.split(ssh_command(recipe, "/srv/project"))
        self.assertIsNone(capture_ssh_recipe([*argv, "extra-command"]))
        argv[5] = "PermitLocalCommand=yes"
        self.assertIsNone(capture_ssh_recipe(argv))


class NativeTest(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_original_and_replacement_are_rejected_in_both_orders(self):
        original = types.SimpleNamespace(session_id="original", async_get_variable=AsyncMock(return_value="Default"))
        replacement = types.SimpleNamespace(session_id="replacement", async_get_variable=AsyncMock(return_value="Changed profile"))
        runner = types.SimpleNamespace(conn=None, snapshot={"windows": [{"tabs": [{"panes": [{"id": "original"}]}]}]},
                                      job={"id": "0" * 16, "steps": {"original": {"session": "replacement"}}}, step=AsyncMock())
        for sessions in ([replacement, original], [original, replacement]):
            runner.app = types.SimpleNamespace(async_refresh=AsyncMock(), terminal_windows=[types.SimpleNamespace(tabs=[types.SimpleNamespace(all_sessions=sessions)])])
            native = NativeRestore(runner, None)
            with patch("fbbridge.recovery_native.session_state", AsyncMock(return_value="live")):
                with self.assertRaisesRegex(UserError, "Duplicate recovery identities"):
                    await native.discover(profiles=False)

    async def test_journal_messages_respect_utf8_byte_limit(self):
        capture = types.SimpleNamespace(conn=None, app=None, call=AsyncMock())
        runner = Runner(capture)
        runner.job = {"steps": {}}
        await runner.step("pane", "deviation", "目录" * 300)
        message = runner.job["steps"]["pane"]["message"]
        self.assertLessEqual(len(message.encode("utf-8")), 512)
        self.assertTrue(message)

    async def test_partial_busy_recovery_never_grows_or_resizes_the_tab(self):
        pane = {"id": "first", "cwd": "/Users/alex/project", "connection": {"kind": "shell"}}
        runner = types.SimpleNamespace(conn=None, app=None, job={"id": "0" * 16, "steps": {}}, step=AsyncMock())
        runner.capture = types.SimpleNamespace(call=AsyncMock(return_value={"retired": []}))
        native = NativeRestore(runner, None)
        tab = types.SimpleNamespace(tab_id="owned-tab")
        session = types.SimpleNamespace(session_id="new", tab=tab, async_get_variable=AsyncMock(return_value=native.marker("first")))
        native.app = types.SimpleNamespace(get_session_by_id=lambda sid: session if sid == "new" else None)
        native.mapping = {"first": "new"}
        native.check = AsyncMock()
        native.split_tree = AsyncMock()
        saved = {"control": False, "panes": [pane, {**pane, "id": "second"}],
                 "tree": {"vertical": True, "children": [{"pane": "first"}, {"pane": "second"}]}}
        with patch("fbbridge.recovery_native.resolve", AsyncMock(return_value={"cwd": pane["cwd"], "busy": True})):
            with self.assertRaisesRegex(UserError, "busy or changed"):
                await native.tab(saved, None)
        native.split_tree.assert_not_awaited()

    async def test_new_ssh_process_never_inherits_a_different_hosts_directory(self):
        values = {"hostname": "alpha.example", "path": "/srv/alpha"}
        pid, argv = [42], ["ssh", "alpha.example"]
        async def variable(key): return values.get(key)
        s = types.SimpleNamespace(session_id="remote", async_get_variable=variable, grid_size=types.SimpleNamespace(width=80, height=24))
        capture = Capture(None, None, None, None)
        with patch("fbbridge.recovery_capture.resolve", AsyncMock(return_value={"mode": "remote", "job": "ssh"})), \
             patch.object(capture, "profile", AsyncMock(return_value={})), \
             patch("fbbridge.recovery_capture.static_vars", AsyncMock(return_value=(10, "owned-tty"))), \
             patch("fbbridge.recovery_capture.procinfo.proc_children", return_value=[]), \
             patch("fbbridge.recovery_capture.procinfo.foreground_pid", side_effect=lambda _: pid[0]), \
             patch("fbbridge.recovery_capture.procinfo.proc_name", return_value="ssh"), \
             patch("fbbridge.recovery_capture.procinfo.proc_start", side_effect=lambda p: p), \
             patch("fbbridge.recovery_capture.procinfo.proc_argv", side_effect=lambda _: argv):
            first = await capture.pane(s, False)
            self.assertEqual(first["cwd"], "/srv/alpha")
            pid[0] = 43; argv[-1] = "beta.example"
            second = await capture.pane(s, False)
            self.assertIsNone(second["cwd"])
            self.assertEqual(second["cwd_status"], "unknown")
            values.update(hostname="beta.example", path="/srv/beta")
            third = await capture.pane(s, False)
            self.assertEqual(third["cwd"], "/srv/beta")
            self.assertEqual(third["cwd_status"], "known")
            argv[:] = shlex.split(ssh_command(["beta.example"], "/srv/beta"))
            recaptured = await capture.pane(s, False)
            self.assertEqual(recaptured["connection"], {"kind": "ssh", "args": ["beta.example"]})
            self.assertEqual(recaptured["cwd"], "/srv/beta")

    async def test_busy_changed_and_profile_changed_journal_ids_are_preserved(self):
        session = types.SimpleNamespace(session_id="new-guid", async_get_variable=AsyncMock(return_value="Changed profile"))
        app = types.SimpleNamespace(async_refresh=AsyncMock(), terminal_windows=[types.SimpleNamespace(tabs=[types.SimpleNamespace(all_sessions=[session])])],
                                    get_session_by_id=lambda sid: session if sid == "new-guid" else None)
        pane = {"id": "old-guid", "cwd": "/Users/alex/project", "connection": {"kind": "shell"}}
        runner = types.SimpleNamespace(app=app, conn=None, snapshot={"windows": [{"tabs": [{"panes": [pane]}]}]},
                                      job={"id": "0" * 16, "steps": {"old-guid": {"session": "new-guid"}}}, step=AsyncMock())
        native = NativeRestore(runner, None)
        with patch("fbbridge.recovery_native.session_state", AsyncMock(return_value="live")):
            await native.discover(profiles=False)
        self.assertIs(native.session("old-guid"), session)
        for result in ({"cwd": pane["cwd"], "busy": True}, {"cwd": "/Users/alex/changed", "busy": False}):
            with patch("fbbridge.recovery_native.resolve", AsyncMock(return_value=result)):
                self.assertFalse(await native.inspect(pane, session))
                self.assertEqual(runner.step.call_args.args[1], "deviation")


class RemoteTmuxTest(unittest.IsolatedAsyncioTestCase):
    async def test_verified_remote_identity_and_unavailable_authentication(self):
        args, host, socket = ["alex@devbox.example"], "devbox.example", "/tmp/owned-socket"
        digest = hashlib.sha256(json.dumps([host, args], separators=(",", ":")).encode()).hexdigest()[:16]
        server = {"id": f"tmux:devbox:{socket}:{digest}", "local": False, "args": args, "socket": socket, "pid": 42, "started": 100}
        session = {"id": "$0", "created": 200, "windows": [{"panes": [{"id": "%0"}]}]}
        restore = TmuxRestore(types.SimpleNamespace(), {"servers": [server]})
        output = f"{host}\t{socket}\t42\t100\t$0\t200\n"
        with patch("fbbridge.recovery_tmux.local_command", AsyncMock(return_value=output)) as probe:
            verified = await restore.original(server, session)
            self.assertTrue(verified["live"])
            argv = probe.call_args.args[0]
            for flag in ("BatchMode=yes", "StrictHostKeyChecking=yes", "RemoteCommand=none", "PermitLocalCommand=no"):
                self.assertIn(flag, argv)
            restore.ready[(server["id"], session["id"])] = verified
            command = await restore.command({"server": server["id"], "session": "$0", "pane": "%0", "control": True})
            wrapped = shlex.split(shlex.split(command)[-1])
            self.assertEqual(wrapped[:2], ["/bin/sh", "-c"])
            bootstrap = wrapped[2]
            self.assertIn("tmux -N", bootstrap)
            self.assertIn("session_created", bootstrap)
            self.assertIn(shlex.quote(verified["proof"]), bootstrap)
        with tempfile.TemporaryDirectory(prefix="fb-remote-probe-") as root:
            reply, attached, fake = Path(root) / "reply", Path(root) / "attached", Path(root) / "tmux"
            fake.write_text('#!/bin/sh\ncase "$*" in\n*display-message*) cat "$FB_TEST_REPLY";;\n*attach-session*) : > "$FB_TEST_ATTACH";;\n*) exit 2;;\nesac\n')
            fake.chmod(0o700)
            env = {"PATH": root + ":/usr/bin:/bin", "FB_TEST_REPLY": str(reply), "FB_TEST_ATTACH": str(attached)}
            reply.write_text(output)
            result = await asyncio.to_thread(subprocess.run, ["/bin/sh", "-c", bootstrap], env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertTrue(attached.exists())
            attached.unlink()
            reply.write_text(output.replace("\t42\t", "\t43\t"))
            result = await asyncio.to_thread(subprocess.run, ["/bin/sh", "-c", bootstrap], env=env,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(attached.exists(), "post-auth identity mismatch prevents attach")
        for changed in (output.replace("\t42\t", "\t43\t"), output.replace("\t100\t", "\t101\t"),
                        output.replace("\t$0\t", "\t$1\t"), output.replace("\t200\n", "\t201\n"),
                        output.replace(host, "other.example")):
            with patch("fbbridge.recovery_tmux.local_command", AsyncMock(return_value=changed)):
                with self.assertRaisesRegex(UserError, "could not be verified"):
                    await restore.original(server, session)
        with patch("fbbridge.recovery_tmux.local_command", AsyncMock(side_effect=UserError("authentication unavailable"))):
            with self.assertRaisesRegex(UserError, "authenticate manually"):
                await restore.original(server, session)


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class TmuxTest(unittest.IsolatedAsyncioTestCase):
    async def test_private_graph_live_identity_recreate_layout_and_retry(self):
        with tempfile.TemporaryDirectory(prefix="fb-tmux-test-", dir="/tmp") as root:
            socket = str(Path(root) / "source")
            async def run(*args):
                return await local_command([shutil.which("tmux"), "-S", socket, *args])
            job_id = uuid.uuid4().hex[:16]
            folder = Path("/tmp") / f"fb-tmux-{os.getuid()}-{job_id}"
            try:
                dirs = []
                for i in range(4):
                    d = Path(root) / str(i); d.mkdir(); dirs.append(str(d.resolve()))
                await run("-f", "/dev/null", "new-session", "-d", "-s", "owned", "-c", dirs[0], "-x", "140", "-y", "45", "/bin/sh")
                await run("split-window", "-h", "-t", "owned:0", "-c", dirs[1], "/bin/sh")
                await run("split-window", "-v", "-t", "owned:0", "-c", dirs[2], "/bin/sh")
                await run("move-window", "-s", "owned:0", "-t", "owned:3")
                await run("new-window", "-d", "-t", "owned:5", "-c", dirs[3], "/bin/sh")
                pane_id = (await run("display-message", "-p", "-t", "owned:3", "#{pane_id}")).strip()
                session = types.SimpleNamespace(session_id="native-test", tab=types.SimpleNamespace(tmux_connection_id="test"),
                                                async_get_variable=AsyncMock(return_value=pane_id.lstrip("%")))
                async def send(text):
                    return await run(*shlex.split(text))
                tc = types.SimpleNamespace(async_send_command=send)
                graph = TmuxGraphs(None)
                with patch("fbbridge.recovery_tmux_capture.iterm2.async_get_tmux_connection_by_connection_id", AsyncMock(return_value=tc), create=True), \
                     patch("fbbridge.recovery_tmux_capture.gateway_target", AsyncMock(return_value=None)):
                    recipe = await graph.connection(session, True)
                server = next(iter(graph.servers.values()))
                saved = server["sessions"][0]
                self.assertEqual([w["index"] for w in saved["windows"]], [3, 5])
                self.assertEqual(sum(len(w["panes"]) for w in saved["windows"]), 4)
                runner = types.SimpleNamespace(job={"id": job_id, "steps": {}}, step=AsyncMock())
                restore = TmuxRestore(runner, {"servers": [server]})
                live = await restore.original(server, saved)
                self.assertTrue(live["live"])
                self.assertIsNone(await restore.original({**server, "started": server["started"] - 1}, saved))
                await run("kill-server")
                self.assertIsNone(await restore.original(server, saved))
                recreated = await restore.private(server, saved)
                for w in saved["windows"]:
                    target = recreated["session"] + ":" + str(w["index"])
                    rows = await restore.run(recreated["socket"], "list-panes", "-t", target, "-F", "#{pane_id}\t#{pane_current_path}\t#{pane_current_command}")
                    found = {row.split("\t")[0]: row.split("\t")[1:] for row in rows.splitlines()}
                    self.assertEqual(len(found), len(w["panes"]))
                    for p in w["panes"]:
                        self.assertEqual(found[recreated["panes"][p["id"]]][0], p["cwd"])
                        self.assertIn(found[recreated["panes"][p["id"]]][1], ("sh", "dash", "bash"))
                    actual = (await restore.run(recreated["socket"], "display-message", "-p", "-t", target, "#{window_layout}")).strip()
                    self.assertEqual(actual, remap_layout(w["layout"], recreated["panes"]))
                again = await restore.private(server, saved)
                self.assertEqual(recreated["panes"], again["panes"])
                selected = saved["windows"][1]["panes"][0]["id"]
                restore.ready[(server["id"], saved["id"])] = recreated
                command = await restore.command({**recipe, "pane": selected, "control": False})
                args = shlex.split(command)
                self.assertIn("-N", args)
                self.assertIn("active-pane", args)
                self.assertEqual(args[-1], recreated["panes"][selected])
                self.assertEqual(args[-3], "select-pane")
                if sys.platform == "darwin":
                    # script gives this owned tmux client its own controlling TTY.
                    process = subprocess.Popen(["/usr/bin/script", "-q", "/dev/null", *args], stdin=subprocess.PIPE,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               env=dict(os.environ, TERM="xterm-256color"), start_new_session=True)
                    try:
                        for _ in range(30):
                            rows = await restore.run(recreated["socket"], "list-clients", "-F", "#{client_tty}")
                            if rows.strip(): break
                            await asyncio.sleep(0.1)
                        tty = rows.strip().splitlines()[0]
                        observed = (await restore.run(recreated["socket"], "display-message", "-c", tty, "-p", "#{pane_id}")).strip()
                        self.assertEqual(observed, recreated["panes"][selected], "client restores its recorded second-window pane")
                        current = (await restore.run(recreated["socket"], "display-message", "-p", "-t", recreated["session"], "#{window_index}")).strip()
                        self.assertEqual(current, "3", "group attachment does not switch the original session")
                    finally:
                        process.stdin.close()
                        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM)
                        await asyncio.to_thread(process.wait, 3)
                # A crash leaves a partial graph; subsequent user activity must
                # stop retry before any split, resize or active-pane mutation.
                # Reset fixture sizes after the independent TTY client's resize.
                for w in saved["windows"]:
                    width, height = re.match(r"[0-9a-f]+,(\d+)x(\d+)", w["layout"]).groups()
                    target = recreated["session"] + ":" + str(w["index"])
                    await restore.run(recreated["socket"], "resize-window", "-t", target, "-x", width, "-y", height)
                    await restore.run(recreated["socket"], "select-layout", "-t", target, remap_layout(w["layout"], recreated["panes"]))
                first_window = saved["windows"][0]
                keep, remove = first_window["panes"][0], first_window["panes"][-1]
                target_pane = recreated["panes"][keep["id"]]
                await restore.run(recreated["socket"], "kill-pane", "-t", recreated["panes"][remove["id"]])
                async def topology():
                    return await restore.run(recreated["socket"], "list-windows", "-t", recreated["session"], "-F",
                                             "#{window_index}\t#{window_layout}\t#{pane_id}")
                async def input_and_wait(command, field, expected):
                    await restore.run(recreated["socket"], "send-keys", "-t", target_pane, command, "Enter")
                    for _ in range(30):
                        value = (await restore.run(recreated["socket"], "display-message", "-p", "-t", target_pane, field)).strip()
                        if value == expected: return
                        await asyncio.sleep(0.1)
                    self.fail("owned tmux pane did not reach fixture state")
                await input_and_wait("sleep 30", "#{pane_current_command}", "sleep")
                before = await topology()
                with self.assertRaisesRegex(UserError, "busy or its directory changed"):
                    await restore.private(server, saved)
                self.assertEqual(await topology(), before)
                await restore.run(recreated["socket"], "send-keys", "-t", target_pane, "C-c")
                await input_and_wait("cd " + shlex.quote(dirs[3]), "#{pane_current_path}", dirs[3])
                before = await topology()
                with self.assertRaisesRegex(UserError, "busy or its directory changed"):
                    await restore.private(server, saved)
                self.assertEqual(await topology(), before)
                await input_and_wait("cd " + shlex.quote(keep["cwd"]), "#{pane_current_path}", keep["cwd"])
                recreated = await restore.private(server, saved)
                self.assertEqual(len(recreated["panes"]), 4, "idle partial graph can finish")
                await restore.run(recreated["socket"], "select-layout", "-t", recreated["session"] + ":3", "even-horizontal")
                before = await topology()
                with self.assertRaisesRegex(UserError, "layout changed or completion is ambiguous"):
                    await restore.private(server, saved)
                self.assertEqual(await topology(), before)
                runner.job["steps"]["tmux:" + server["id"] + ":" + saved["id"]] = {"state": "restored"}
                await restore.run(recreated["socket"], "kill-window", "-t", recreated["session"] + ":5")
                with self.assertRaisesRegex(UserError, "topology changed"):
                    await restore.private(server, saved)
                remaining = (await restore.run(recreated["socket"], "list-windows", "-t", recreated["session"], "-F", "#{window_index}")).splitlines()
                self.assertEqual(remaining, ["3"], "retry does not recreate a user-deleted window")
                # If the verified server vanishes before terminal launch, attach
                # must not start one or execute even a harmless config sentinel.
                sentinel, config = Path(root) / "config-ran", Path(root) / "tmux.conf"
                config.write_text("run-shell " + shlex.quote("touch " + shlex.quote(str(sentinel))) + "\n")
                await restore.run(recreated["socket"], "kill-server")
                with self.assertRaises(UserError):
                    await local_command([args[0], "-f", str(config), *args[1:]])
                self.assertFalse(sentinel.exists())
                Path(dirs[0]).rmdir()
                runner.job["steps"] = {}
                restore = TmuxRestore(runner, {"servers": [server]})
                with patch("fbbridge.recovery_tmux.os.path.expanduser", return_value=root):
                    fallback = await restore.private(server, saved)
                self.assertEqual(len(fallback["panes"]), 4, "one deleted cwd does not prevent reconstructing the graph")
                missing = next(p for w in saved["windows"] for p in w["panes"] if p["cwd"] == dirs[0])
                rows = await restore.run(fallback["socket"], "display-message", "-p", "-t", fallback["panes"][missing["id"]],
                                         "#{pane_current_path}\t#{pane_current_command}")
                path, command = rows.strip().split("\t")
                self.assertEqual(Path(path).resolve(), Path.home().resolve())
                self.assertIn(command, ("sh", "dash", "bash"))
                self.assertTrue(any(call.args[1] == "deviation" and "directory unavailable" in call.args[2]
                                    for call in runner.step.call_args_list))
                first = next(w for w in saved["windows"] if any(p["id"] == missing["id"] for p in w["panes"]))
                lost = next(p for p in first["panes"] if p["id"] != missing["id"])
                await restore.run(fallback["socket"], "kill-pane", "-t", fallback["panes"][lost["id"]])
                retried = await restore.private(server, saved)
                self.assertEqual(len(retried["panes"]), 4, "partial retry accepts only the expected missing-directory fallback")
            finally:
                try: await run("kill-server")
                except Exception: pass
                if folder.exists():
                    for path in folder.iterdir():
                        try: await local_command([shutil.which("tmux"), "-S", str(path), "kill-server"])
                        except Exception: pass
                    shutil.rmtree(folder)


if __name__ == "__main__":
    unittest.main()
