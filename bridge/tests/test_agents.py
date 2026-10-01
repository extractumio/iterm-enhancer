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
        from fbbridge import common, agentctl, remote
        importlib.reload(common)
        self.ctl = importlib.reload(agentctl)
        self.remote = importlib.reload(remote)
        self.root = root
        self.assertTrue(str(self.ctl.RECORD).startswith(self.tmp.name), "the record must be the test's own")

    @staticmethod
    def restore_modules():
        from fbbridge import common, agentctl, remote
        for m in (common, agentctl, remote):
            importlib.reload(m)

    def fake_agent(self, agent_id):
        f = self.root / f"fbd-{agent_id}"
        f.write_text(f"#!/bin/sh\necho {agent_id}\n")
        f.chmod(0o755)
        return f

    def test_probe_maps_platforms_and_refuses_others(self):
        with mock.patch.object(self.ctl, "ssh", lambda alias, cmd, **k: "Linux x86_64\nubuntu\nalex\n"):
            self.assertEqual(self.ctl.probe("vm"), ("ubuntu", "alex", "linux-x86_64"))
        with mock.patch.object(self.ctl, "ssh", lambda alias, cmd, **k: "Darwin arm64\nStudio.local\nalex\n"):
            self.assertEqual(self.ctl.probe("mac"), ("studio", "alex", "macos-aarch64"))
        with mock.patch.object(self.ctl, "ssh", lambda alias, cmd, **k: "FreeBSD amd64\nbsd\nalex\n"), \
                self.assertRaises(self.ctl.AgentError):
            self.ctl.probe("bsd")

    def test_ssh_failure_carries_ssh_message(self):
        with mock.patch.dict(os.environ, {"FAKE_SSH_FAIL": "Permission denied (publickey)."}), \
                self.assertRaises(self.ctl.AgentError) as cm:
            self.ctl.ssh("vm", "true")
        self.assertIn("Permission denied", str(cm.exception))

    def test_install_links_current_and_keeps_two(self):
        lib = self.root / "home" / self.ctl.REMOTE_LIB
        a, b, c, d = ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc", "dddddddddddd")
        for aid in (a, b, c):
            self.ctl.install("vm", self.fake_agent(aid), aid)
            time.sleep(0.01)
        self.assertEqual(os.readlink(lib / "current"), c)
        self.assertEqual(sorted(p.name for p in lib.iterdir() if p.name != "current"), [b, c])
        with self.assertRaises(self.ctl.AgentError):
            self.ctl.install("vm", self.fake_agent(d), "eeeeeeeeeeee")  # answers d, not e
        with self.assertRaises(self.ctl.AgentError):
            self.ctl.install("vm", self.fake_agent(d), "x; rm -rf ~")  # never into a shell line

    def test_tunnel_reports_why_it_failed(self):
        with mock.patch.dict(os.environ, {"FAKE_SSH_FAIL": "connect to host vm port 22: Connection refused"}):
            importlib.reload(self.ctl)
            t = self.ctl.Tunnel("vm", self.ctl.socket_for("vm"))
            with self.assertRaises(self.ctl.AgentError) as cm:
                t.start(timeout=5)
        self.assertIn("Connection refused", str(cm.exception))

    def test_prepare_refuses_a_host_name_of_another_alias(self):
        import agent as prepare_cmd
        importlib.reload(prepare_cmd)
        self.ctl.save({"vm1": {"host": "ubuntu", "platform": "linux-x86_64"}})
        with mock.patch.object(self.ctl, "probe", lambda alias: ("ubuntu", "alex", "linux-x86_64")), \
                self.assertRaises(self.ctl.AgentError) as cm:
            prepare_cmd.prepare("vm2", build=lambda ps: {})
        self.assertIn("already used by vm1", str(cm.exception))
        self.assertNotIn("vm2", self.ctl.load())

    def test_prepare_records_the_host(self):
        import agent as prepare_cmd
        importlib.reload(prepare_cmd)
        aid = prepare_cmd.agents.agent_id()
        binary = self.fake_agent(aid)
        with mock.patch.object(self.ctl, "probe", lambda alias: ("devbox", "alex", "linux-x86_64")), \
                mock.patch.object(prepare_cmd, "trial", lambda alias: (aid, "devbox", "alex", "linux-x86_64")):
            msg = prepare_cmd.prepare("devbox", build=lambda ps: {ps[0]: binary})
        self.assertIn("devbox ready", msg)
        self.assertEqual(self.ctl.load()["devbox"], {"host": "devbox", "user": "alex", "platform": "linux-x86_64", "agent_id": aid})
        self.assertEqual(self.ctl.alias_for("devbox"), "devbox")

    def test_remotes_without_agent_say_how(self):
        r = self.remote.Remotes(lambda path, body: None)
        up, note = r.status("devbox")
        self.assertFalse(up)
        self.assertIn("make agent HOST=", note)

    def test_remotes_back_off_after_a_failure(self):
        self.ctl.save({"vm": {"host": "devbox"}})
        posted = []
        r = self.remote.Remotes(lambda path, body: posted.append(body))
        with mock.patch.object(self.remote.Remotes, "_connect", side_effect=self.ctl.AgentError("vm: refused")):
            self.assertEqual(r.status("devbox")[0], False)
            for _ in range(50):
                if r.hosts["devbox"]["status"] == "down":
                    break
                time.sleep(0.02)
            up, note = r.status("devbox")
        self.assertFalse(up)
        self.assertEqual(note, "vm: refused")
        self.assertEqual(r.hosts["devbox"]["backoff"], 2.0, "the next try waits longer")
        self.assertEqual(posted, [], "nothing registered with fbd")


    def test_an_outdated_agent_is_replaced_from_the_running_build(self):
        self.ctl.save({"vm": {"host": "devbox", "agent_id": "old"}})
        binary = self.root / "build/agents/linux-x86_64/fbd"
        binary.parent.mkdir(parents=True)
        binary.write_text("new agent")
        answers = iter([("old", "devbox", "alex", "linux-x86_64"), ("new", "devbox", "alex", "linux-x86_64")])

        class FakeTunnel:
            def __init__(self, alias, local):
                self.local, self.token = local, "t"

            def start(self):
                return next(answers)

            def stop(self):
                pass
        installed = []
        with mock.patch.object(self.remote, "LOCAL_AGENT_ID", "new"), \
                mock.patch.object(self.remote, "BUILD_DIR", self.root / "build"), \
                mock.patch.object(self.remote.agentctl, "Tunnel", FakeTunnel), \
                mock.patch.object(self.remote.agentctl, "install", lambda alias, b, aid: installed.append((alias, b, aid))):
            self.remote.Remotes(lambda p, b: None)._connect("vm")
        self.assertEqual(installed, [("vm", binary, "new")])
        self.assertEqual(self.ctl.load()["vm"]["agent_id"], "new")

    def test_an_outdated_agent_without_a_build_says_how(self):
        class FakeTunnel:
            def __init__(self, alias, local):
                self.local, self.token = local, "t"

            def start(self):
                return ("old", "devbox", "alex", "linux-riscv64")

            def stop(self):
                pass
        with mock.patch.object(self.remote, "LOCAL_AGENT_ID", "new"), \
                mock.patch.object(self.remote.agentctl, "Tunnel", FakeTunnel), \
                self.assertRaises(self.ctl.AgentError) as cm:
            self.remote.Remotes(lambda p, b: None)._connect("vm")
        self.assertIn("agent outdated · make agent HOST=vm", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
