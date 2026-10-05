# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Repeated group reconstruction and independent owned tmux clients."""
import asyncio
import hashlib
import os
import shlex
import shutil
import signal
import socket as sockets
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge.common import UserError
from fbbridge.recovery_tmux import TmuxRestore, private_absent
from fbbridge.recovery_tmux_capture import TmuxGraphs, local_command


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class GroupTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="fb-group-test-", dir="/tmp")
        self.socket = str(Path(self.root.name) / "source")
        self.sockets = {self.socket}
        self.folders = []
        self.clients = []
        await local_command([shutil.which("tmux"), "-S", self.socket, "-f", "/dev/null", "new-session",
                             "-d", "-s", "owned", "-x", "120", "-y", "40", "/bin/sh"])
        await self.tmux(self.socket, "new-window", "-d", "-t", "owned:1", "/bin/sh")
        await self.tmux(self.socket, "new-session", "-d", "-s", "second", "-t", "owned")

    async def asyncTearDown(self):
        await self.stop_clients()
        for socket in self.sockets:
            try: await self.tmux(socket, "kill-server")
            except UserError: pass
        for folder in self.folders:
            shutil.rmtree(folder, ignore_errors=True)
        self.root.cleanup()

    async def tmux(self, socket, *args):
        return await local_command([shutil.which("tmux"), "-N", "-S", socket, *args])

    async def capture(self, socket, targets, ttys=None):
        graph = TmuxGraphs(None)
        async def reader(session, control):
            async def run(args):
                # Detached fixtures address their source session explicitly;
                # owned TTY clients use the production display-message -c path.
                if args[0] == "display-message" and not ttys:
                    args = [args[0], "-t", targets[session.session_id], *args[1:]]
                return await self.tmux(socket, *args)
            return run, [], True, ttys.get(session.session_id) if ttys else None
        graph.reader = reader
        recipes = []
        for native in targets:
            recipes.append(await graph.connection(types.SimpleNamespace(session_id=native), False))
        server = next(iter(graph.servers.values()))
        self.warnings = graph.warnings
        return server, recipes

    def restore(self, server):
        job = {"id": uuid.uuid4().hex[:16], "steps": {}}
        async def step(key, state, message, *args):
            job["steps"][key] = {"state": state}
        runner = types.SimpleNamespace(job=job, step=step)
        self.folders.append(Path("/tmp") / f"fb-tmux-{os.getuid()}-{job['id']}")
        return TmuxRestore(runner, {"servers": [server]})

    async def attach(self, restore, recipes):
        commands = [shlex.split(await restore.command(recipe, client_id=f"native-{i}")) for i, recipe in enumerate(recipes)]
        names = [args[args.index("-t") + 1].split(":")[0] for args in commands]
        self.assertEqual(len(set(names)), 2, "same-pane clients have distinct helper sessions")
        socket = commands[0][commands[0].index("-S") + 1]
        for args in commands:
            self.clients.append(subprocess.Popen(["/usr/bin/script", "-q", "/dev/null", *args], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=dict(os.environ, TERM="xterm-256color"), start_new_session=True))
        for _ in range(50):
            rows = await self.tmux(socket, "list-clients", "-F", "#{session_name}\t#{client_tty}")
            found = dict(row.split("\t") for row in rows.splitlines())
            if all(name in found for name in names):
                return socket, names, {f"native-{i}": found[name] for i, name in enumerate(names)}
            await asyncio.sleep(0.1)
        self.fail("owned TTY clients did not attach")

    async def stop_clients(self):
        for process in self.clients:
            process.stdin.close()
            if process.poll() is None:
                try: os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError: pass
            await asyncio.to_thread(process.wait, 3)
        self.clients.clear()

    async def view(self, socket, tty, field):
        rows = await self.tmux(socket, "list-clients", "-F", "#{client_tty}\t" + field)
        return next(value for device, value in (row.split("\t") for row in rows.splitlines()) if device == tty)

    def assert_graph(self, server, recipes):
        groups = {s.get("group") for s in server["sessions"]}
        self.assertEqual(len(groups), 1)
        self.assertNotIn(None, groups)
        self.assertNotEqual(recipes[0]["session"], recipes[1]["session"])
        for session in server["sessions"]:
            self.assertEqual(len(session["windows"]), 2)
            self.assertEqual(sum(len(w["panes"]) for w in session["windows"]), 2,
                             "list-panes -a duplicate group rows are deduplicated")

    @unittest.skipUnless(sys.platform == "darwin", "owned script TTY fixture requires macOS")
    async def test_two_full_generations_share_graph_and_keep_same_pane_clients_independent(self):
        server, recipes = await self.capture(self.socket, {"native-0": "owned:0", "native-1": "second:0"})
        self.assert_graph(server, recipes)
        self.assertEqual(recipes[0]["pane"], recipes[1]["pane"])
        await self.tmux(self.socket, "kill-server")
        for generation in range(2):
            restore = self.restore(server)
            # Restore the higher SID first to challenge representative selection.
            second = await restore.connection(recipes[1])
            first = await restore.connection(recipes[0])
            self.sockets.add(first["socket"])
            self.assertNotEqual(first["session"], second["session"])
            self.assertEqual(first["panes"], second["panes"])
            self.assertEqual(first, await restore.connection(recipes[0]))
            controls = [shlex.split(await restore.command({**recipe, "control": True})) for recipe in recipes]
            self.assertNotEqual(controls[0][controls[0].index("-t") + 1], controls[1][controls[1].index("-t") + 1],
                                "separate control gateways retain their session identities")
            # Both start on the same recorded pane, even after the prior capture.
            recipes[1] = {**recipes[1], "pane": recipes[0]["pane"]}
            socket, names, ttys = await self.attach(restore, recipes)
            for tty in ttys.values():
                pane = await self.view(socket, tty, "#{pane_id}")
                self.assertEqual(pane, first["panes"][recipes[0]["pane"]])
            initial = await self.view(socket, ttys["native-1"], "#{window_index}")
            alternate = "1" if initial == "0" else "0"
            await self.tmux(socket, "select-window", "-t", names[0] + ":" + alternate)
            current = [await self.view(socket, tty, "#{window_index}") for tty in ttys.values()]
            self.assertEqual(current, [alternate, initial], "switching one client preserves the other")
            server, recipes = await self.capture(socket, dict(zip(ttys, names)), ttys)
            self.assert_graph(server, recipes)
            self.assertIn("tmux client-local selected pane unavailable; window active pane used", self.warnings)
            self.assertNotEqual(recipes[0]["pane"], recipes[1]["pane"], "capture tracks each client's actual window")
            rows = await self.tmux(socket, "list-windows", "-a", "-F", "#{window_id}")
            self.assertEqual(len(set(rows.splitlines())), 2, f"generation {generation} has one physical graph")
            self.assertEqual(len(first["panes"]), 2)
            await self.stop_clients()
            await self.tmux(socket, "kill-server")

    async def client_pane(self, socket, tty, index):
        result = Path(self.root.name) / f"client-pane-{index}"
        command = "run-shell " + shlex.quote("printf '%s' '#{pane_id}' > " + shlex.quote(str(result)))
        # Only this fixture's client receives keys; production capture never
        # opens prompts or injects keys to obtain otherwise unavailable state.
        await self.tmux(socket, "send-keys", "-K", "-c", tty, "C-b", ":")
        await self.tmux(socket, "send-keys", "-K", "-c", tty, "-l", command)
        await self.tmux(socket, "send-keys", "-K", "-c", tty, "Enter")
        for _ in range(50):
            if result.exists():
                return result.read_text()
            await asyncio.sleep(0.1)
        self.fail("owned client's command queue did not report its pane")

    @unittest.skipUnless(sys.platform == "darwin", "owned script TTY fixture requires macOS")
    async def test_unobservable_client_pane_is_reported_instead_of_claiming_exact_capture(self):
        await self.tmux(self.socket, "split-window", "-d", "-h", "-t", "owned:0", "/bin/sh")
        server, recipes = await self.capture(self.socket, {"one": "owned:0", "two": "second:0"})
        await self.tmux(self.socket, "kill-server")
        restore = self.restore(server)
        restored = await restore.connection(recipes[0]); self.sockets.add(restored["socket"])
        recipes[1]["pane"] = next(p["id"] for w in server["sessions"][0]["windows"] if w["index"] == 0
                                  for p in w["panes"] if p["id"] != recipes[0]["pane"])
        socket, names, ttys = await self.attach(restore, recipes)
        actual = [await self.client_pane(socket, tty, i) for i, tty in enumerate(ttys.values())]
        self.assertEqual(actual, [restored["panes"][r["pane"]] for r in recipes], "owned clients do select independent panes")
        _, captured = await self.capture(socket, dict(zip(ttys, names)), ttys)
        self.assertEqual(captured[0]["pane"], captured[1]["pane"], "formats expose the shared window's active pane")
        self.assertNotEqual(actual[0], actual[1])
        self.assertIn("tmux client-local selected pane unavailable; window active pane used", self.warnings)

    async def test_unrelated_linked_windows_are_not_inferred_as_a_group(self):
        await self.tmux(self.socket, "new-session", "-d", "-s", "unrelated", "/bin/sh")
        await self.tmux(self.socket, "link-window", "-s", "owned:0", "-t", "unrelated:1")
        server, recipes = await self.capture(self.socket, {"one": "owned:0", "two": "unrelated:1"})
        saved = {s["id"]: s for s in server["sessions"]}
        self.assertIn("group", saved[recipes[0]["session"]])
        self.assertNotIn("group", saved[recipes[1]["session"]])
        self.assertEqual(recipes[0]["pane"], recipes[1]["pane"])

    async def test_partially_surviving_original_group_preserves_live_apps_in_either_recipe_order(self):
        server, recipes = await self.capture(self.socket, {"one": "owned:0", "two": "second:0"})
        await self.tmux(self.socket, "kill-session", "-t", "second")
        before = await self.tmux(self.socket, "list-sessions", "-F", "#{session_id}")
        for order in ((0, 1), (1, 0)):
            restore = self.restore(server)
            for index in order:
                if index == 0:
                    original = await restore.connection(recipes[0])
                    self.assertTrue(original["live"])
                    self.assertEqual(original["socket"], self.socket)
                    self.assertEqual(original["session"], recipes[0]["session"])
                else:
                    with self.assertRaisesRegex(UserError, "surviving sessions and applications preserved"):
                        await restore.connection(recipes[1])
            self.assertEqual(await self.tmux(self.socket, "list-sessions", "-F", "#{session_id}"), before)
            self.assertEqual(restore.private_ready, {})

    async def test_completed_private_server_loss_and_crash_during_durable_reset_can_retry(self):
        server, recipes = await self.capture(self.socket, {"one": "owned:0", "two": "second:0"})
        await self.tmux(self.socket, "kill-server")
        restore = self.restore(server)
        first = await restore.connection(recipes[0]); self.sockets.add(first["socket"])
        await restore.connection(recipes[1])
        for order in ((0, 1), (1, 0)):
            await self.tmux(first["socket"], "kill-server")
            for _ in range(50):
                if private_absent(first["socket"]):
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(private_absent(first["socket"]), "owned server finished closing its listener")
            # A crash may leave a Unix socket inode with no listener.
            if not os.path.lexists(first["socket"]):
                with sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM) as stale:
                    stale.bind(first["socket"])
            steps, real_step = restore.runner.job["steps"], restore.runner.step
            async def failed_reset(key, state, message, *args):
                if state == "pending" and key.endswith(recipes[1]["session"]):
                    raise UserError("journal storage unavailable")
                await real_step(key, state, message, *args)
            restore.runner.step = failed_reset
            retry = TmuxRestore(restore.runner, {"servers": [server]})
            with self.assertRaisesRegex(UserError, "journal storage unavailable"):
                await retry.connection(recipes[order[0]])
            self.assertEqual(steps["tmux:" + server["id"] + ":" + recipes[0]["session"]]["state"], "pending")
            self.assertEqual(steps["tmux:" + server["id"] + ":" + recipes[1]["session"]]["state"], "restored")
            with self.assertRaises(UserError):
                await self.tmux(first["socket"], "list-sessions")
            restore.runner.step = real_step
            retry = TmuxRestore(restore.runner, {"servers": [server]})
            restored = [await retry.connection(recipes[i]) for i in order]
            self.assertEqual(restored[0]["panes"], restored[1]["panes"])
            self.assertNotEqual(restored[0]["session"], restored[1]["session"])
            windows = await self.tmux(first["socket"], "list-windows", "-a", "-F", "#{window_id}")
            self.assertEqual(len(set(windows.splitlines())), 2)
            restore = retry

    async def test_unknown_private_socket_is_preserved_without_reset_or_reconstruction(self):
        server, _ = await self.capture(self.socket, {"one": "owned:0", "two": "second:0"})
        restore = self.restore(server)
        folder = self.folders[-1]; folder.mkdir(mode=0o700)
        path = folder / hashlib.sha256(server["id"].encode()).hexdigest()[:12]
        target = folder / "owned-sentinel"; target.write_text("preserve")
        path.symlink_to(target)
        restore.run = AsyncMock(side_effect=UserError("server unavailable"))
        with self.assertRaisesRegex(UserError, "cannot be verified; preserved"):
            await restore.private(server, server["sessions"][0])
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(), "preserve")
        self.assertEqual(restore.runner.job["steps"], {})

    async def test_retry_preserves_conflicting_or_deleted_completed_alias_and_lead(self):
        server, recipes = await self.capture(self.socket, {"one": "owned:0", "two": "second:0"})
        await self.tmux(self.socket, "kill-server")
        restore = self.restore(server)
        lead = await restore.connection(recipes[0])
        alias = await restore.connection(recipes[1])
        self.sockets.add(lead["socket"])
        async def topology():
            return await self.tmux(lead["socket"], "list-windows", "-a", "-F", "#{session_name}\t#{window_id}\t#{window_layout}")
        await self.tmux(lead["socket"], "set-option", "-t", alias["session"], "@fb-restore", "user-change")
        before = await topology()
        with self.assertRaisesRegex(UserError, "group identity changed"):
            await restore.group(server, server["sessions"][1])
        self.assertEqual(await topology(), before)
        await self.tmux(lead["socket"], "kill-session", "-t", alias["session"])
        before = await topology()
        with self.assertRaisesRegex(UserError, "session was removed"):
            await restore.group(server, server["sessions"][1])
        self.assertEqual(await topology(), before)
        # Keep one verified member alive, then remove the completed lead.
        await self.tmux(lead["socket"], "new-session", "-d", "-s", "survivor", "-t", lead["session"])
        await self.tmux(lead["socket"], "kill-session", "-t", lead["session"])
        before = await topology()
        with self.assertRaisesRegex(UserError, "session was removed"):
            await restore.private(server, server["sessions"][0])
        self.assertEqual(await topology(), before)
