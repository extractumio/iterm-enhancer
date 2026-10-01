# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-37 / AC-38: preparing hosts and keeping their agents connected, with a fake `ssh`
that runs the command on this Mac under a temporary home (no network, no real host).
The real tunnel is exercised by scripts/e2e_remote.sh against a container with sshd."""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bridge"))
sys.path.insert(0, str(REPO / "scripts"))

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
        env = {"FB_SSH": str(ssh), "FB_APP_DIR": str(root / "app"), "FAKE_HOME": str(root / "home")}
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
        self.assertTrue(str(self.ctl.RECORD).startswith(self.tmp.name), "the record must be the test's own")

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
        self.assertEqual(os.readlink(home / "bin/fbd"), f"fbd-{c}")
        self.assertEqual(sorted(p.name for p in (home / "bin").iterdir()), ["fbd", f"fbd-{b}", f"fbd-{c}"])
        self.assertTrue((home / "logs").is_dir())
        self.assertEqual(oct(home.stat().st_mode & 0o777), "0o700")
        self.ctl.remove(["vm"])
        self.assertFalse(home.exists(), "remove takes it all off the host")
        with self.assertRaises(self.ctl.AgentError):
            self.ctl.install(["vm"], self.fake_agent(d), "eeeeeeeeeeee")  # answers d, not e
        with self.assertRaises(self.ctl.AgentError):
            self.ctl.install(["vm"], self.fake_agent(d), "x; rm -rf ~")  # never into a shell line

    def test_tunnel_reports_why_it_failed(self):
        with mock.patch.dict(os.environ, {"FAKE_SSH_FAIL": "connect to host vm port 22: Connection refused"}):
            importlib.reload(self.ctl)
            t = self.ctl.Tunnel(["vm"], self.ctl.socket_for("vm"))
            with self.assertRaises(self.ctl.AgentError) as cm:
                t.start(timeout=5)
        self.assertIn("Connection refused", str(cm.exception))

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
        self.ctl.RECORD.write_text(json.dumps({"ai4": {"host": "ai4", "user": "alex", "platform": "linux-x86_64", "agent_id": "x"}}))
        self.assertEqual(self.ctl.load()["ai4"]["ssh"], ["ai4"])

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
        gate = __import__("threading").Event()

        class SlowTunnel:
            local, token = Path("/nonexistent.sock"), "t"

            def stop(self):
                stopped.append(True)

        def slow_connect(_self, key, target):
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

    def test_an_outdated_agent_is_replaced_from_the_running_build(self):
        self.ctl.save({"vm": {"ssh": ["vm"], "name": "devbox", "agent_id": "old"}})
        binary = self.root / "build/agents/linux-x86_64/fbd"
        binary.parent.mkdir(parents=True)
        binary.write_text("new agent")
        answers = iter([("old", "devbox", "alex", "linux-x86_64"), ("new", "devbox", "alex", "linux-x86_64")])

        class FakeTunnel:
            def __init__(self, target, local):
                self.local, self.token = local, "t"

            def start(self):
                return next(answers)

            def stop(self):
                pass
        installed = []
        with mock.patch.object(self.remote.hosts, "LOCAL_AGENT_ID", "new"), \
                mock.patch.object(self.remote, "BUILD_DIR", self.root / "build"), \
                mock.patch.object(self.remote.agentctl, "Tunnel", FakeTunnel), \
                mock.patch.object(self.remote.agentctl, "install", lambda target, b, aid: installed.append((target, b, aid))):
            self.remote.Remotes(lambda p, b: None)._connect("vm", ["vm"])
        self.assertEqual(installed, [(["vm"], binary, "new")])
        self.assertEqual(self.ctl.load()["vm"]["agent_id"], "new")

    def test_an_outdated_agent_without_a_build_says_how(self):
        class FakeTunnel:
            def __init__(self, target, local):
                self.local, self.token = local, "t"

            def start(self):
                return ("old", "devbox", "alex", "linux-riscv64")

            def stop(self):
                pass
        with mock.patch.object(self.remote.hosts, "LOCAL_AGENT_ID", "new"), \
                mock.patch.object(self.remote.agentctl, "Tunnel", FakeTunnel), \
                self.assertRaises(self.ctl.AgentError) as cm:
            self.remote.Remotes(lambda p, b: None)._connect("vm", ["vm"])
        self.assertIn("iterm-filebrowser hosts enable vm", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
