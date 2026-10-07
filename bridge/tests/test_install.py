# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-33 / AC-35: install, upgrade, rollback and uninstall on a temporary home, with
iTerm2 faked (no launch, no health from a real fbd)."""
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "bridge"))


def fake_package(dir_, build, answers=None):
    """A package folder as scripts/package.py makes it, with an fbd that says `answers`."""
    pkg = Path(dir_) / f"pkg-{build}-{answers or build}"
    shutil.rmtree(pkg, ignore_errors=True)
    for plat in ("macos-aarch64", "macos-x86_64", "linux-x86_64"):
        f = pkg / "agents" / plat / "fbd"
        f.parent.mkdir(parents=True)
        f.write_text(f"#!/bin/sh\necho {answers or build}\n")
        f.chmod(0o755)
    (pkg / "bridge/fbbridge").mkdir(parents=True)
    (pkg / "bridge/fbbridge/app.py").write_text("# bridge\n")
    (pkg / "bridge/fb_bridge.py").write_text("# entry\n")
    (pkg / "iterm-filebrowser").write_text("#!/bin/sh\n")
    (pkg / "BUILD").write_text(build + "\n")
    return pkg


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        root = self.home / ".iterm-filebrowser"
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})  # every path the installer uses derives from HOME
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("FB_APP_DIR", None)  # restored with the rest of the environment
        import install_launch
        import install
        self.live = importlib.reload(install_launch)
        self.inst = importlib.reload(install)
        self.root = root
        self.addCleanup(self.tmp.cleanup)
        # nothing this test can delete may lie outside the temporary home
        for p in (self.inst.LIB, self.inst.BIN, self.inst.AUTOLAUNCH, self.inst.APP_DIR, self.live.APP_DIR):
            self.assertTrue(str(p).startswith(self.tmp.name), f"{p} is outside the test's home")
        for p in (self.inst.PROFILE, self.inst.OLD_LIB, self.inst.OLD_BIN, *self.inst.OLD_DIRS):
            self.assertTrue(str(p).startswith(self.tmp.name), f"{p} is outside the test's home")
        self.running = {}            # what the fake iTerm2 runs: {"build": ..., "bridge": ...}
        self.launches = []
        self.healthy_builds = set()  # builds that come up when launched
        m = [mock.patch.object(self.live, "iterm_running", lambda: True),
             mock.patch.object(self.inst.setup_state, "wait_result", lambda directory, request_id: {"id": request_id, "status": "done", "changes": [], "errors": []}),
             mock.patch.object(self.live, "launch", self.fake_launch),
             mock.patch.object(self.live, "health", lambda legacy=False: dict(self.running) or None),
             mock.patch.object(self.live, "wait_healthy", lambda build, seconds=10.0, legacy=False: self.running.get("build") == build),
             mock.patch.object(self.inst, "say", lambda msg: self.said.append(msg))]
        self.said = []
        for p in m:
            p.start()
            self.addCleanup(p.stop)

    def fake_launch(self):
        build = os.readlink(self.root / "builds/current")
        self.launches.append(build)
        self.running = ({"build": build, "bridge_build": build, "bridge_connected": True}
                        if build in self.healthy_builds else {})

    def install(self, build, healthy=True):
        if healthy:
            self.healthy_builds.add(build)
        self.inst.install(fake_package(self.tmp.name, build))

    def current(self, name="current"):
        link = self.root / "builds" / name
        return os.readlink(link) if link.is_symlink() else None

    def test_fresh_install_upgrade_same_build_and_prune(self):
        self.install("a")
        self.assertEqual(self.current(), "a")
        self.assertEqual(os.readlink(self.root / "bin/fbd"), str(self.root / "builds/current/fbd"))
        self.assertTrue((self.inst.AUTOLAUNCH / "fb_bridge.py").is_file())
        self.assertEqual((self.root / "builds/a/BUILD").read_text().strip(), "a")
        self.assertTrue((self.root / "builds/a/bridge/fbbridge/app.py").is_file())
        self.assertTrue((self.root / "builds/a/agents/linux-x86_64/fbd").is_file(), "the helpers for remote hosts come along")
        self.assertTrue(os.readlink(self.root / "builds/a/fbd").startswith("agents/macos-"), "the Mac's fbd is its macOS helper")
        self.assertEqual(os.readlink(self.root / "bin/iterm-filebrowser"), str(self.root / "builds/a/iterm-filebrowser"))
        self.install("b")
        self.assertEqual((self.current(), self.current("previous")), ("b", "a"))
        self.assertIn("Upgraded a → b", self.said)
        n = len(self.launches)
        self.install("b")
        self.assertEqual(len(self.launches), n, "same build, running: nothing relaunched")
        self.assertIn("b is already installed and running", self.said)
        self.install("c")
        self.assertEqual(sorted(p.name for p in (self.root / "builds").iterdir() if not p.name.startswith(".") and p.is_dir()
                                and not p.is_symlink()), ["b", "c"], "only current and previous are kept")

    def test_unhealthy_build_is_rolled_back(self):
        self.install("a")
        with self.assertRaises(self.inst.Failed) as cm:
            self.install("b", healthy=False)
        self.assertIn("rolled back to a", str(cm.exception))
        self.assertEqual(self.current(), "a")
        self.assertEqual(self.launches[-2:], ["b", "a"])
        self.assertTrue((self.root / "builds/b").is_dir(), "kept for a look; pruned by the next good install")

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
        self.assertTrue((self.root / "builds/a").is_dir(), "nothing pruned before the new build was seen running")

    def test_wrong_binary_is_refused_before_anything_changes(self):
        with self.assertRaises(self.inst.Failed):
            self.inst.install(fake_package(self.tmp.name, "b", answers="other"))
        self.assertIsNone(self.current())
        self.assertFalse((self.root / "bin/fbd").exists())

    def old_layout(self, build="z"):
        """An install as Stage 7 to 9 left it: builds in ~/.local/lib, links in ~/.local/bin,
        state in Application Support, logs in Library/Logs."""
        old_app, old_logs = list(self.inst.OLD_DIRS)
        old_app.mkdir(parents=True)
        (old_app / "token").write_text("same-token")
        (old_app / "workspaces.json").write_text("{}")
        old_logs.mkdir(parents=True)
        (old_logs / "fbd.log").write_text("old log")
        for b in (build, "y"):
            (self.inst.OLD_LIB / b / "bridge/fbbridge").mkdir(parents=True)
            (self.inst.OLD_LIB / b / "bridge/fbbridge/common.py").write_text("# the old paths\n")
            (self.inst.OLD_LIB / b / "fbd").write_text("#!/bin/sh\n")
        (self.inst.OLD_LIB / "current").symlink_to(build)
        (self.inst.OLD_LIB / "previous").symlink_to("y")
        self.healthy_builds.add("y")
        self.inst.OLD_BIN.mkdir(parents=True)
        for name in ("fbd", "iterm-filebrowser"):
            (self.inst.OLD_BIN / name).symlink_to(self.inst.OLD_LIB / "current" / name)
        (self.inst.OLD_BIN / "other-tool").write_text("not ours")
        self.healthy_builds.add(build)
        return old_app, old_logs

    def test_moves_the_old_layout_under_one_root(self):
        old_app, old_logs = self.old_layout()
        self.install("a")
        self.assertEqual((self.root / "state/token").read_text(), "same-token", "the Toolbelt URL stays valid")
        self.assertEqual((self.root / "logs/fbd.log").read_text(), "old log")
        self.assertEqual((self.current(), self.current("previous")), ("a", "z"), "the old build stays for a rollback")
        self.assertEqual(old_app.resolve(), (self.root / "state").resolve(), "linked while build z may run again")
        self.assertFalse(self.inst.OLD_LIB.exists())
        self.assertEqual(sorted(p.name for p in self.inst.OLD_BIN.iterdir()), ["other-tool"])
        self.inst.rollback()
        self.assertEqual(self.launches[-1], "z")
        self.assertTrue((self.root / "builds/z/bridge/fbbridge/common.py").is_file())
        self.assertEqual(os.readlink(self.root / "bin/iterm-filebrowser"), str(self.root / "builds/a/iterm-filebrowser"),
                         "after a rollback into an old build the command still knows this layout")
        self.install("b")
        self.install("c")
        self.assertFalse(old_app.is_symlink() or old_app.exists(), "no kept build uses the old folders")
        self.assertFalse(old_logs.is_symlink() or old_logs.exists())
        self.assertEqual((self.root / "state/token").read_text(), "same-token")

    def test_old_state_merges_into_a_new_folder_and_a_clash_is_refused(self):
        old_app, _ = self.old_layout()
        (self.root / "state").mkdir(parents=True)
        (self.root / "state/agents.json").write_text("{}")
        self.install("a")
        self.assertEqual((self.root / "state/token").read_text(), "same-token")
        self.assertTrue((self.root / "state/agents.json").is_file())
        self.assertTrue(old_app.is_symlink())

    def test_a_token_in_both_places_is_refused(self):
        old_app, _ = self.old_layout()
        (self.root / "state").mkdir(parents=True)
        (self.root / "state/token").write_text("new-token")
        with self.assertRaises(self.inst.Failed) as cm:
            self.install("a")
        self.assertIn("both exist", str(cm.exception))
        self.assertEqual((self.root / "state/token").read_text(), "new-token")
        self.assertEqual((old_app / "token").read_text(), "same-token", "nothing of the user's is deleted")
        self.assertIsNone(self.current(), "nothing installed")

    def test_uninstall_before_install_keeps_the_old_token(self):
        self.old_layout()
        with mock.patch.object(self.inst.subprocess, "run"):
            self.inst.uninstall()
        self.install("a")
        self.assertEqual((self.root / "state/token").read_text(), "same-token")

    def test_a_bad_package_moves_nothing(self):
        old_app, old_logs = self.old_layout()
        with self.assertRaises(self.inst.Failed):
            self.inst.install(fake_package(self.tmp.name, "b", answers="other"))
        self.assertFalse(old_app.is_symlink() or old_logs.is_symlink())
        self.assertFalse((self.root / "state").exists())

    def test_a_log_written_while_moving_is_merged(self):
        _, old_logs = self.old_layout()
        real = Path.symlink_to

        def bridge_logs(path, dest, *a):
            if path == old_logs and not (old_logs / "bridge.log").exists() and not hasattr(self, "logged"):
                self.logged = True
                old_logs.mkdir()
                (old_logs / "bridge.log").write_text("late line\n")
            return real(path, dest, *a)
        with mock.patch.object(Path, "symlink_to", bridge_logs):
            self.inst.move_old_layout()
        self.assertTrue(old_logs.is_symlink())
        self.assertEqual((self.root / "logs/bridge.log").read_text(), "late line\n")
        self.assertEqual((self.root / "logs/fbd.log").read_text(), "old log")

    def test_rollback_from_the_old_layout(self):
        self.old_layout()
        self.inst.rollback()
        self.assertEqual((self.current(), self.current("previous")), ("y", "z"))

    def test_migrates_the_unversioned_layout(self):
        self.inst.OLD_BIN.mkdir(parents=True)
        (self.inst.OLD_BIN / "fbd").write_text("old binary: reads FB_APP_DIR")
        old_app, _ = self.inst.OLD_DIRS
        (old_app / "bridge/fbbridge").mkdir(parents=True)
        self.inst.move_old_layout()
        self.install("a")
        self.assertTrue((self.root / "bin/fbd").is_symlink())
        self.assertFalse((self.inst.OLD_BIN / "fbd").exists(), "the copied fbd of installs before Stage 7 goes")
        self.assertFalse((self.root / "state/bridge").exists(), "the old bridge copy goes once the new build runs")

    def test_an_fbd_that_is_not_ours_stays(self):
        self.inst.OLD_BIN.mkdir(parents=True)
        (self.inst.OLD_BIN / "fbd").write_text("someone else's tool")
        self.install("a")
        self.assertTrue((self.inst.OLD_BIN / "fbd").is_file())

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
        (self.root / "builds/.b.tmp/bridge").mkdir(parents=True)  # a copy that was interrupted
        self.install("b")
        self.assertTrue((self.root / "builds/b/fbd").is_file())
        self.assertFalse((self.root / "builds/.b.tmp").exists())

    def test_the_root_is_private(self):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)  # as an earlier install left it
        os.close(self.inst.lock())
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_one_install_at_a_time(self):
        fd = self.inst.lock()
        try:
            with self.assertRaises(self.inst.Failed):
                self.inst.lock()
        finally:
            os.close(fd)

    def test_uninstall_keeps_token_and_workspaces(self):
        self.install("a")
        (self.root / "state").mkdir(parents=True, exist_ok=True)
        (self.root / "state/token").write_text("t")
        self.inst.PROFILE.parent.mkdir(parents=True, exist_ok=True)
        self.inst.PROFILE.write_text("{}")
        with mock.patch.object(self.inst.subprocess, "run"):
            self.inst.uninstall()
        self.assertFalse(self.inst.PROFILE.exists())
        self.assertFalse((self.root / "builds").exists())
        self.assertFalse((self.root / "bin/fbd").exists() or (self.root / "bin/fbd").is_symlink())
        self.assertTrue((self.root / "state/token").exists())

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


    # The result line and the PATH tip: colors only on a terminal that takes them, and no
    # tip for a user who has done what it says.

    def tty(self, tty=True, term="xterm-256color", no_color=False):
        env = {"TERM": term}
        if no_color:
            env["NO_COLOR"] = "1"
        stdout = mock.Mock(isatty=lambda: tty)
        for p in (mock.patch.object(sys, "stdout", stdout), mock.patch.dict(os.environ, env)):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("NO_COLOR", None) if not no_color else None  # the patched copy of the environment

    def test_colors_only_where_they_work(self):
        self.tty()
        self.assertTrue(self.inst.color())
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertFalse(self.inst.color(), "NO_COLOR")
        with mock.patch.dict(os.environ, {"TERM": "dumb"}):
            self.assertFalse(self.inst.color(), "a dumb terminal")
        self.tty(tty=False)
        self.assertFalse(self.inst.color(), "a pipe or a log file")

    def test_the_result_has_a_green_check_on_a_color_terminal_and_none_elsewhere(self):
        self.tty()
        self.inst.done("Upgraded a → b")
        self.assertEqual(self.said[-1], "\033[32m✓\033[0m Upgraded a → b")
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.inst.done("Upgraded a → b")
        self.assertEqual(self.said[-1], "Upgraded a → b")

    def tip(self):
        self.said.clear()
        self.inst.path_tip()
        return list(self.said)

    def test_the_tip_follows_a_blank_line_and_is_gray_on_a_color_terminal(self):
        self.tty()
        said = self.tip()
        self.assertEqual(said[0], "")
        self.assertTrue(said[1].startswith("\033[38;5;69mTip: add ") and said[1].endswith("\033[0m"), said)
        self.tty(tty=False)
        said = self.tip()
        self.assertEqual((said[0], said[1].startswith("Tip: add ")), ("", True))
        self.assertNotIn("\033", said[1])

    def test_no_tip_when_the_command_is_reachable_or_the_user_set_it_up(self):
        self.tty()
        with mock.patch.dict(os.environ, {"PATH": f"/usr/bin:{self.inst.BIN}"}):
            self.assertEqual(self.tip(), [], "on PATH")
        zshrc = self.home / ".zshrc"
        zshrc.write_text("# mine\nexport PATH=\"$HOME/.iterm-filebrowser/bin:$PATH\"\n")
        self.assertEqual(self.tip(), [], "added to .zshrc, this shell has not read it yet")
        zshrc.write_text("  # export PATH=\"$HOME/.iterm-filebrowser/bin:$PATH\"\n")
        self.assertEqual(len(self.tip()), 2, "a comment does not count")
        zshrc.unlink()
        (self.home / ".config/fish").mkdir(parents=True)
        (self.home / ".config/fish/config.fish").write_text("fish_add_path ~/.iterm-filebrowser/bin\n")
        self.assertEqual(self.tip(), [], "fish")
        (self.home / ".config/fish/config.fish").write_bytes(b"\xff\xfe not text .iterm-filebrowser/bin")
        self.assertEqual(self.tip(), [], "unreadable text is not a crash")

    def test_a_zdotdir_counts(self):
        self.tty()
        zdot = self.home / "zdot"
        zdot.mkdir()
        (zdot / ".zshrc").write_text("path+=(~/.iterm-filebrowser/bin)\n")
        with mock.patch.dict(os.environ, {"ZDOTDIR": str(zdot)}):
            self.assertEqual(self.tip(), [])


if __name__ == "__main__":
    unittest.main()
