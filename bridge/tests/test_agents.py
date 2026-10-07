# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-37 / AC-38 / AC-42: preparing hosts and keeping their agents connected, with a fake `ssh`
that runs the command on this Mac under a temporary home (no network, no real host).
The real tunnel is exercised by scripts/e2e_remote.sh against a container with sshd."""
import asyncio
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bridge"))
sys.path.insert(0, str(REPO / "scripts"))
sys.modules.setdefault("iterm2", __import__("types").ModuleType("iterm2"))  # resolve needs it, not these tests

FAKE_SSH = """#!/bin/sh
# fake ssh: the last argument is the remote command; run it here, in the fake home
for last; do :; done
[ -n "$FAKE_SSH_FAIL" ] && { echo "$FAKE_SSH_FAIL" >&2; exit 255; }
HOME="$FAKE_HOME" exec sh -c "$last"
"""


class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "home").mkdir()
        ssh = root / "ssh"
        ssh.write_text(FAKE_SSH)
        ssh.chmod(0o755)
        env = {"FB_SSH": str(ssh), "FB_APP_DIR": str(root / "app"), "FAKE_HOME": str(root / "home"), "HOME": str(root / "mac")}
        self.addCleanup(self.restore_modules)  # runs after the environment is restored
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        from fbbridge import common, agentctl, hosts, remote
        importlib.reload(common)
        self.ctl = importlib.reload(agentctl)
        importlib.reload(hosts)
        self.remote = importlib.reload(remote)
        self.root = root
        for p in (self.ctl.RECORD, common.LOG_DIR):
            self.assertTrue(str(p).startswith(self.tmp.name), f"{p} must be the test's own")
        self.addCleanup(self.join_removals)  # first: a removal still logging writes into the test's home

    @staticmethod
    def join_removals():
        for t in threading.enumerate():
            if t.name.startswith("remove-"):
                t.join(5)

    @staticmethod
    def restore_modules():
        from fbbridge import common, agentctl, hosts, remote
        for m in (common, agentctl, hosts, remote):
            importlib.reload(m)

    def fake_agent(self, agent_id):
        f = self.root / f"fbd-{agent_id}"
        f.write_text(f"#!/bin/sh\necho {agent_id}\n")
        f.chmod(0o755)
        return f

    def test_probe_maps_platforms_and_refuses_others(self):
        with mock.patch.object(self.ctl, "ssh", lambda alias, cmd, **k: "Linux x86_64\nubuntu\nalex\n"):
            self.assertEqual(self.ctl.probe(["vm"]), ("ubuntu", "alex", "linux-x86_64"))
        with mock.patch.object(self.ctl, "ssh", lambda alias, cmd, **k: "Darwin arm64\nStudio.local\nalex\n"):
            self.assertEqual(self.ctl.probe(["mac"]), ("studio", "alex", "macos-aarch64"))
        with mock.patch.object(self.ctl, "ssh", lambda alias, cmd, **k: "FreeBSD amd64\nbsd\nalex\n"), \
                self.assertRaises(self.ctl.AgentError):
            self.ctl.probe(["bsd"])

    def test_ssh_failure_carries_ssh_message(self):
        with mock.patch.dict(os.environ, {"FAKE_SSH_FAIL": "Permission denied (publickey)."}), \
                self.assertRaises(self.ctl.AgentError) as cm:
            self.ctl.ssh(["vm"], "true")
        self.assertIn("Permission denied", str(cm.exception))

    def test_install_links_current_and_keeps_two(self):
        home = self.root / "home" / self.ctl.HOME_DIR
        a, b, c, d = ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd")
        for aid in (a, b, c):
            self.ctl.install(["vm"], self.fake_agent(aid), aid)
            time.sleep(0.01)
        self.assertEqual(os.readlink(home / "bin/fbd-agent"), f"fbd-agent-{c}")
        self.assertEqual(sorted(p.name for p in (home / "bin").iterdir()), ["fbd-agent", f"fbd-agent-{b}", f"fbd-agent-{c}"])
        self.assertTrue((home / "logs").is_dir())
        self.assertEqual(oct(home.stat().st_mode & 0o777), "0o700")
        (home / "logs/agent.log").write_text("x")
        self.ctl.remove(["vm"])
        self.assertFalse(home.exists(), "remove takes it all off the host")
        with self.assertRaises(self.ctl.AgentError):
            self.ctl.install(["vm"], self.fake_agent(d), "eeeeeeeeeeee")  # answers d, not e
        with self.assertRaises(self.ctl.AgentError):
            self.ctl.install(["vm"], self.fake_agent(d), "x; rm -rf ~")  # never into a shell line

    def test_install_and_remove_leave_a_mac_hosts_own_install(self):
        home = self.root / "home" / self.ctl.HOME_DIR
        for rel in ("bin/fbd", "bin/iterm-enhancer", "logs/fbd.log", "state/token"):
            (home / rel).parent.mkdir(parents=True, exist_ok=True)
            (home / rel).write_text("mine")
        aid = "aaaaaaaaaaaa"
        self.ctl.install(["mac"], self.fake_agent(aid), aid)
        self.assertEqual((home / "bin/fbd").read_text(), "mine")
        (home / "logs/agent.log").write_text("x")
        self.ctl.remove(["mac"])
        self.assertEqual(sorted(str(p.relative_to(home)) for p in home.rglob("*") if p.is_file()),
                         ["bin/fbd", "bin/iterm-enhancer", "logs/fbd.log", "state/token"])

    def test_tunnel_reports_why_it_failed(self):
        with mock.patch.dict(os.environ, {"FAKE_SSH_FAIL": "connect to host vm port 22: Connection refused"}):
            importlib.reload(self.ctl)
            t = self.ctl.Tunnel(["vm"], self.ctl.socket_for("vm"))
            with self.assertRaises(self.ctl.AgentError) as cm:
                t.start(timeout=5)
        self.assertIn("Connection refused", str(cm.exception))

    def test_two_macs_on_one_host_each_run_their_own_helper(self):
        b = self.root / "home/.iterm-enhancer/bin"
        b.mkdir(parents=True)
        for aid in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
            f = b / f"fbd-agent-{aid}"
            f.write_text(f"#!/bin/sh\necho fbd-agent ready {aid} devbox alex linux-x86_64\ncat >/dev/null\n")
            f.chmod(0o755)
        (b / "fbd-agent").symlink_to("fbd-agent-bbbbbbbbbbbb")  # the other Mac installed last
        for own, want in (("aaaaaaaaaaaa", "aaaaaaaaaaaa"), ("cccccccccccc", "bbbbbbbbbbbb"), (None, "bbbbbbbbbbbb")):
            t = self.ctl.Tunnel(["vm"], self.ctl.socket_for("vm"), own)
            try:
                self.assertEqual(t.start(timeout=5)[0], want, f"Mac of {own}")
            finally:
                t.stop()

    def test_enable_records_the_host_by_its_ssh_arguments(self):
        hosts = importlib.reload(importlib.import_module("fbbridge.hosts"))
        aid = "abcdef012345"
        binary = self.fake_agent(aid)
        with mock.patch.object(self.ctl, "probe", lambda target: ("ubuntu", "alex", "linux-x86_64")), \
                mock.patch.object(hosts, "trial", lambda target: (aid, "ubuntu", "alex", "linux-x86_64")):
            hosts.enable(["vm1"], binary_for=lambda p: binary, agent_id=aid)
            hosts.enable(["-p", "2222", "alex@vm2"], binary_for=lambda p: binary, agent_id=aid)
        record = self.ctl.load()
        self.assertEqual(sorted(record), ["-p 2222 alex@vm2", "vm1"], "two hosts, though both say ubuntu")
        self.assertEqual(record["vm1"], {"ssh": ["vm1"], "name": "ubuntu", "user": "alex", "platform": "linux-x86_64", "agent_id": aid})
        self.assertEqual(self.ctl.entry("-p 2222 alex@vm2")["ssh"], ["-p", "2222", "alex@vm2"])

    def test_a_stage8_record_still_reads(self):
        self.ctl.RECORD.parent.mkdir(parents=True, exist_ok=True)
        self.ctl.RECORD.write_text(json.dumps({"devbox.example": {"host": "devbox.example", "user": "alex", "platform": "linux-x86_64", "agent_id": "x"}}))
        self.assertEqual(self.ctl.load()["devbox.example"]["ssh"], ["devbox.example"])

    def wait(self, r, key, status):
        for _ in range(100):
            if r.hosts.get(key, {}).get("status") == status:
                return
            time.sleep(0.02)
        self.fail(f"{key} never became {status}: {r.hosts.get(key)}")

    def test_a_new_host_is_offered_until_not_now(self):
        r = self.remote.Remotes(lambda path, body: None)
        self.assertEqual(r.status("devbox", ["devbox"], "devbox")["state"], "ask")
        r.dismiss("devbox")
        st = r.status("devbox", ["devbox"], "devbox")
        self.assertEqual((st["state"], st["enabled"]), ("dismissed", False))

    def test_a_failed_enable_says_why_and_can_be_retried(self):
        posted = []
        r = self.remote.Remotes(lambda path, body: posted.append((path, body)))
        r.status("devbox", ["devbox"], "devbox")
        with mock.patch.object(self.remote.hosts, "enable", side_effect=self.ctl.AgentError("devbox: Permission denied (publickey).")):
            r.enable("devbox", by="panel1")
            self.wait(r, "devbox", "new")
        self.assertIn(("/internal/error", {"message": "Could not enable devbox: devbox: Permission denied (publickey).", "by": "panel1"}), posted)
        st = r.status("devbox", ["devbox"], "devbox")
        self.assertEqual(st["state"], "ask", "Enable can be tried again")
        self.assertIn("Permission denied", st["note"])

    def test_an_enabled_host_connects_and_backs_off_after_a_failure(self):
        self.ctl.save({"devbox": {"ssh": ["devbox"], "name": "devbox"}})
        posted = []
        r = self.remote.Remotes(lambda path, body: posted.append(body))
        with mock.patch.object(self.remote.Remotes, "_connect", side_effect=self.ctl.AgentError("devbox: refused")):
            self.assertTrue(r.status("devbox", ["devbox"], "devbox")["enabled"])
            self.wait(r, "devbox", "down")
            st = r.status("devbox", ["devbox"], "devbox")
        self.assertEqual((st["state"], st["note"]), ("down", "devbox: refused"))
        self.assertEqual(r.hosts["devbox"]["backoff"], 2.0, "the next try waits longer")
        self.assertEqual(posted, [], "nothing registered with fbd")

    def test_remove_forgets_the_host_and_takes_the_helper_off(self):
        self.ctl.save({"devbox": {"ssh": ["devbox"], "name": "devbox"}})
        removed = []
        r = self.remote.Remotes(lambda path, body: None)
        with mock.patch.object(self.remote.agentctl, "remove", lambda target: removed.append(target)):
            r.remove("devbox")
            for _ in range(100):
                if removed:
                    break
                time.sleep(0.02)
        self.assertEqual(removed, [["devbox"]])
        self.assertIsNone(self.ctl.entry("devbox"))
        self.assertEqual(r.status("devbox", ["devbox"], "devbox")["state"], "dismissed", "not offered again right away")

    def test_remove_while_connecting_leaves_no_connection(self):
        self.ctl.save({"devbox": {"ssh": ["devbox"], "name": "devbox"}})
        posted, stopped = [], []
        gate = threading.Event()

        class SlowTunnel:
            local, token = Path("/nonexistent.sock"), "t"

            def stop(self):
                stopped.append(True)

        def slow_connect(_self, key, target, owner=None):
            gate.wait(5)
            return SlowTunnel()
        r = self.remote.Remotes(lambda path, body: posted.append((path, body)))
        with mock.patch.object(self.remote.Remotes, "_connect", slow_connect), \
                mock.patch.object(self.remote.agentctl, "remove", lambda target: None):
            r.status("devbox", ["devbox"], "devbox")            # starts connecting
            r.remove("devbox")                                  # removed meanwhile
            gate.set()
            for _ in range(100):
                if stopped:
                    break
                time.sleep(0.02)
        self.assertEqual(stopped, [True], "the late connection is closed")
        self.assertNotIn("devbox", r.hosts)
        self.assertFalse([p for p in posted if p[0] == "/internal/remote" and p[1].get("socket")], "never registered")

    def outdated(self, install, answers=None):
        """Connect "vm", whose agent says "old", with the running build's "new"; `install`
        plays agentctl.install. Returns the Remotes."""
        self.ctl.save({"vm": {"ssh": ["vm"], "name": "devbox", "agent_id": "old"}})
        binary = self.root / "build/agents/linux-x86_64/fbd"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("new agent")
        answers = iter(answers or [("old", "devbox", "alex", "linux-x86_64"), ("new", "devbox", "alex", "linux-x86_64")])

        class FakeTunnel:
            def __init__(self, target, local, agent_id=None):
                self.local, self.token = local, "t"

            def start(self):
                return next(answers)

            def stop(self):
                pass
        r = self.remote.Remotes(lambda p, b: None)
        with mock.patch.object(self.remote.hosts, "LOCAL_AGENT_ID", "new"), \
                mock.patch.object(self.remote.hosts, "BUILD_DIR", self.root / "build"), \
                mock.patch.object(self.remote, "BUILD", "v9.9.0"), \
                mock.patch.object(self.remote.agentctl, "Tunnel", FakeTunnel), \
                mock.patch.object(self.remote.agentctl, "install", lambda target, b, aid: install(r, target, b, aid)):
            r._connect("vm", ["vm"])
            r._set("vm", status="up")
            st = r.status("vm", ["vm"], "devbox")
        return r, binary, st

    def test_an_outdated_agent_is_replaced_from_the_running_build(self):
        installed, during = [], []

        def install(r, target, b, aid):
            installed.append((target, b, aid))
            during.append(r.status("vm", ["vm"], "devbox"))   # asked meanwhile: no second connection
        r, binary, st = self.outdated(install)
        self.assertEqual(installed, [(["vm"], binary, "new")])
        self.assertEqual(self.ctl.load()["vm"]["agent_id"], "new")
        self.assertEqual((during[0]["state"], during[0]["note"]), ("updating", "updating the helper on devbox…"))
        self.assertEqual([t for t in threading.enumerate() if t.name == "agent-vm"], [], "updating starts no other connection")
        self.assertEqual((st["state"], st["updated"], st["note"]), ("up", "v9.9.0", "devbox (helper updated to v9.9.0)"))
        r._set("vm", updated_at=time.monotonic() - 61)
        st = r.status("vm", ["vm"], "devbox")
        self.assertNotIn("updated", st, "said for a minute")
        self.assertEqual(st["note"], "devbox (helper)")

    def test_a_failed_update_says_so(self):
        def install(r, target, b, aid):
            raise self.ctl.AgentError("vm: No space left on device")
        with self.assertRaises(self.ctl.AgentError) as cm:
            self.outdated(install)
        self.assertEqual(str(cm.exception), "Could not update the helper on devbox: vm: No space left on device")
        self.assertEqual(self.ctl.load()["vm"]["agent_id"], "old")

    def test_a_failed_update_waits_before_trying_again(self):
        self.ctl.save({"vm": {"ssh": ["vm"], "name": "devbox"}})
        r = self.remote.Remotes(lambda p, b: None)
        with mock.patch.object(self.remote.Remotes, "_connect", side_effect=self.remote.UpdateError("Could not update the helper on devbox: full")):
            r.status("vm", ["vm"], "devbox")
            self.wait(r, "vm", "down")
        self.assertGreater(r.hosts["vm"]["retry_at"] - time.monotonic(), 590, "not every 30 s")
        self.assertEqual(r.status("vm", ["vm"], "devbox")["note"], "Could not update the helper on devbox: full")

    def test_a_host_removed_during_an_update_stays_removed(self):
        self.outdated(lambda r, target, b, aid: self.ctl.forget("vm"))
        self.assertNotIn("vm", self.ctl.load())

    def test_the_same_build_copies_nothing_and_says_nothing(self):
        installed = []
        r, _, st = self.outdated(lambda *a: installed.append(a), answers=[("new", "devbox", "alex", "linux-x86_64")])
        self.assertEqual(installed, [])
        self.assertNotIn("updated", st)

    def test_open_windows_of_enabled_hosts_are_found_unfocused(self):
        from fbbridge import resolve
        self.ctl.save({"devbox.example": {"ssh": ["devbox.example"], "name": "devbox.example"}})
        conns = [types.SimpleNamespace(connection_id=n, target=t) for n, t in
                 [(1, ["devbox.example"]), (2, ["devbox.example"]), (3, ["devbox"]), (4, None)]]  # devbox: not enabled; 4: local tmux

        async def gateway(tc):
            return tc.target

        async def connections(conn):
            return conns
        with mock.patch.object(resolve.iterm2, "async_get_tmux_connections", connections, create=True), \
                mock.patch.object(resolve, "gateway_target", gateway):
            self.assertEqual(asyncio.run(resolve.open_hosts(None)), {"devbox.example": ["devbox.example"]})

    def test_an_outdated_agent_without_a_build_says_how(self):
        class FakeTunnel:
            def __init__(self, target, local, agent_id=None):
                self.local, self.token = local, "t"

            def start(self):
                return ("old", "devbox", "alex", "linux-riscv64")

            def stop(self):
                pass
        with mock.patch.object(self.remote.hosts, "LOCAL_AGENT_ID", "new"), \
                mock.patch.object(self.remote.agentctl, "Tunnel", FakeTunnel), \
                self.assertRaises(self.ctl.AgentError) as cm:
            self.remote.Remotes(lambda p, b: None)._connect("vm", ["vm"])
        self.assertIn("iterm-enhancer hosts enable vm", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
