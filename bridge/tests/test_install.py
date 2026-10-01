# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-33 / AC-35: install, upgrade, rollback and uninstall on a temporary home, with
iTerm2 faked (no launch, no health from a real fbd)."""
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "bridge"))


def fake_fbd(dir_, build):
    p = Path(dir_) / f"fbd-{build}"
    p.write_text(f"#!/bin/sh\necho {build}\n")
    p.chmod(0o755)
    return p


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        env = {"FB_LIB_DIR": str(root / "lib"), "FB_BIN_DIR": str(root / "bin"),
               "FB_AUTOLAUNCH_DIR": str(root / "AutoLaunch"), "FB_APP_DIR": str(root / "app")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        import install_launch
        import install
        self.live = importlib.reload(install_launch)
        self.inst = importlib.reload(install)
        self.root = root
        self.addCleanup(self.tmp.cleanup)
        # nothing this test can delete may lie outside the temporary home
        for p in (self.inst.LIB, self.inst.BIN, self.inst.AUTOLAUNCH, self.inst.APP_DIR, self.live.APP_DIR):
            self.assertTrue(str(p).startswith(self.tmp.name), f"{p} is outside the test's home")
        # never the live profile: uninstall deletes it
        p = mock.patch.object(self.inst, "PROFILE", root / "DynamicProfiles/iterm-filebrowser.json")
        p.start()
        self.addCleanup(p.stop)
        self.running = {}            # what the fake iTerm2 runs: {"build": ..., "bridge": ...}
        self.launches = []
        self.healthy_builds = set()  # builds that come up when launched
        m = [mock.patch.object(self.live, "iterm_running", lambda: True),
             mock.patch.object(self.live, "launch", self.fake_launch),
             mock.patch.object(self.live, "health", lambda: dict(self.running) or None),
             mock.patch.object(self.live, "wait_healthy", lambda build, seconds=10.0: self.running.get("build") == build),
             mock.patch.object(self.inst, "say", lambda msg: self.said.append(msg))]
        self.said = []
        for p in m:
            p.start()
            self.addCleanup(p.stop)

    def fake_launch(self):
        build = os.readlink(self.root / "lib/current")
        self.launches.append(build)
        self.running = ({"build": build, "bridge_build": build, "bridge_connected": True}
                        if build in self.healthy_builds else {})

    def install(self, build, healthy=True):
        if healthy:
            self.healthy_builds.add(build)
        self.inst.install(build, fake_fbd(self.tmp.name, build))

    def current(self, name="current"):
        link = self.root / "lib" / name
        return os.readlink(link) if link.is_symlink() else None

    def test_fresh_install_upgrade_same_build_and_prune(self):
        self.install("a")
        self.assertEqual(self.current(), "a")
        self.assertEqual(os.readlink(self.root / "bin/fbd"), str(self.root / "lib/current/fbd"))
        self.assertTrue((self.root / "AutoLaunch/fb_bridge.py").is_file())
        self.assertEqual((self.root / "lib/a/BUILD").read_text().strip(), "a")
        self.assertTrue((self.root / "lib/a/bridge/fbbridge/app.py").is_file())
        self.install("b")
        self.assertEqual((self.current(), self.current("previous")), ("b", "a"))
        self.assertIn("Upgraded a → b", self.said)
        n = len(self.launches)
        self.install("b")
        self.assertEqual(len(self.launches), n, "same build, running: nothing relaunched")
        self.assertIn("b is already installed and running", self.said)
        self.install("c")
        self.assertEqual(sorted(p.name for p in (self.root / "lib").iterdir() if not p.name.startswith(".") and p.is_dir()
                                and not p.is_symlink()), ["b", "c"], "only current and previous are kept")

    def test_unhealthy_build_is_rolled_back(self):
        self.install("a")
        with self.assertRaises(self.inst.Failed) as cm:
            self.install("b", healthy=False)
        self.assertIn("rolled back to a", str(cm.exception))
        self.assertEqual(self.current(), "a")
        self.assertEqual(self.launches[-2:], ["b", "a"])
        self.assertTrue((self.root / "lib/b").is_dir(), "kept for a look; pruned by the next good install")

    def test_refused_launch_keeps_the_switch(self):
        self.install("a")
        def refuse():
            raise self.live.LaunchError(self.live.AUTOMATION_FIX)
        with mock.patch.object(self.live, "launch", refuse), self.assertRaises(self.inst.Failed) as cm:
            self.install("b")
        self.assertIn("Automation", str(cm.exception))
        self.assertEqual(self.current(), "b")

    def test_iterm_not_running_installs_and_waits(self):
        self.install("a")
        with mock.patch.object(self.live, "iterm_running", lambda: False):
            self.install("b")
        self.assertEqual(self.current(), "b")
        self.assertIn("Installed b; takes effect when iTerm2 starts", self.said)
        self.assertTrue((self.root / "lib/a").is_dir(), "nothing pruned before the new build was seen running")

    def test_wrong_binary_is_refused_before_anything_changes(self):
        with self.assertRaises(self.inst.Failed):
            self.inst.install("b", fake_fbd(self.tmp.name, "other"))
        self.assertIsNone(self.current())
        self.assertFalse((self.root / "bin/fbd").exists())

    def test_migrates_the_unversioned_layout(self):
        (self.root / "bin").mkdir()
        (self.root / "bin/fbd").write_text("old binary")
        (self.root / "app/bridge/fbbridge").mkdir(parents=True)
        self.install("a")
        self.assertTrue((self.root / "bin/fbd").is_symlink())
        self.assertFalse((self.root / "app/bridge").exists(), "the old bridge copy goes once the new build runs")

    def test_rollback_and_nothing_to_roll_back(self):
        with self.assertRaises(self.inst.Failed) as cm:
            self.inst.rollback()
        self.assertIn("No previous build", str(cm.exception))
        self.install("a")
        self.install("b")
        self.inst.rollback()
        self.assertEqual((self.current(), self.current("previous")), ("a", "b"))
        self.assertEqual(self.launches[-1], "a")

    def test_a_half_copied_build_is_never_linked(self):
        (self.root / "lib/.b.tmp/bridge").mkdir(parents=True)  # a copy that was interrupted
        self.install("b")
        self.assertTrue((self.root / "lib/b/fbd").is_file())
        self.assertFalse((self.root / "lib/.b.tmp").exists())

    def test_one_install_at_a_time(self):
        fd = self.inst.lock()
        try:
            with self.assertRaises(self.inst.Failed):
                self.inst.lock()
        finally:
            os.close(fd)

    def test_uninstall_keeps_token_and_workspaces(self):
        self.install("a")
        (self.root / "app").mkdir(exist_ok=True)
        (self.root / "app/token").write_text("t")
        self.inst.PROFILE.parent.mkdir(parents=True)
        self.inst.PROFILE.write_text("{}")
        with mock.patch.object(self.inst.subprocess, "run"):
            self.inst.uninstall()
        self.assertFalse(self.inst.PROFILE.exists())
        self.assertFalse((self.root / "lib").exists())
        self.assertFalse((self.root / "bin/fbd").exists() or (self.root / "bin/fbd").is_symlink())
        self.assertTrue((self.root / "app/token").exists())

    def test_build_id_covers_the_bridge(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            for rel in ("bridge/fbbridge/app.py", "fbd/src/main.rs", "ui/src/main.ts"):
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_text("x")
            a = self.inst.build_id(repo)
            self.assertEqual(a, self.inst.build_id(repo), "same sources, same id")
            (repo / "bridge/fbbridge/app.py").write_text("y")
            self.assertNotEqual(a, self.inst.build_id(repo), "a bridge-only change is a new build")


if __name__ == "__main__":
    unittest.main()
