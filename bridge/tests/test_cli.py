# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-40: `iterm-enhancer upgrade` installs a release only when it is signed by the
release key and its checksum matches, from a fake release served as local files (no
network), signed with a throwaway key made for the test."""
import hashlib
import importlib
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "bridge"))


def tar_with(files):
    """files: (name, text, mode), or a TarInfo for a link or a device."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for f in files:
            if isinstance(f, tarfile.TarInfo):
                t.addfile(f)
                continue
            name, text, mode = f
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def keypair(d, name):
    key = Path(d) / name
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(key)], check=True)
    return key


def link(name, target, kind=tarfile.SYMTYPE):
    info = tarfile.TarInfo(name)
    info.type, info.linkname = kind, target
    return info


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.marker = self.root / "installed"
        rel = self.root / "releases/latest/download"
        rel.mkdir(parents=True)
        self.rel = rel
        p = mock.patch.dict(os.environ, {"FB_RELEASE_URL": f"file://{self.root}/releases", "FB_APP_DIR": str(self.root / "app"), "HOME": str(self.root / "home")})
        p.start()
        self.addCleanup(p.stop)
        import cli
        self.cli = importlib.reload(cli)
        self.key = keypair(self.root, "release")
        signers = self.root / "release-signers"
        signers.write_text(f"{self.cli.NAMESPACE} namespaces=\"{self.cli.NAMESPACE}\" {self.key.with_suffix('.pub').read_text()}")
        self.cli.SIGNERS = signers

    def publish(self, files, checksum=None, key=None, release="v9.0.0"):
        data = tar_with(files)
        (self.rel / "iterm-enhancer-macos.tar.gz").write_bytes(data)
        digest = checksum or hashlib.sha256(data).hexdigest()
        sums = self.rel / "SHA256SUMS"
        sums.write_text(f"# release {release}\n{digest}  iterm-enhancer-macos.tar.gz\n")
        (self.rel / "SHA256SUMS.sig").unlink(missing_ok=True)
        if key is not False:
            subprocess.run(["ssh-keygen", "-Y", "sign", "-q", "-f", str(key or self.key), "-n", self.cli.NAMESPACE, str(sums)], check=True)

    def package(self):
        return [("iterm-enhancer/iterm-enhancer", f"#!/bin/sh\necho \"$1\" > {self.marker}\n", 0o755),
                ("iterm-enhancer/BUILD", "v9\n", 0o644)]

    def test_a_good_release_installs(self):
        self.publish(self.package())
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade()
        self.assertEqual(cm.exception.code, 0)
        self.assertEqual(self.marker.read_text().strip(), "install", "the package's own installer ran")

    def test_a_wrong_checksum_installs_nothing(self):
        self.publish(self.package(), checksum="0" * 64)
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade()
        self.assertIn("checksum mismatch", str(cm.exception.code))
        self.assertFalse(self.marker.exists())

    def test_a_member_outside_the_package_is_refused(self):
        self.publish(self.package() + [("../escape", "x", 0o644)])
        with self.assertRaises((SystemExit, Exception)):
            self.cli.upgrade()
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "escape").exists())

    def test_an_unsigned_release_installs_nothing(self):
        self.publish(self.package(), key=False)
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade()
        self.assertIn("download failed", str(cm.exception.code))
        self.assertFalse(self.marker.exists())

    def test_a_release_signed_by_another_key_installs_nothing(self):
        self.publish(self.package(), key=keypair(self.root, "attacker"))
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade()
        self.assertIn("not signed by the release key", str(cm.exception.code))
        self.assertFalse(self.marker.exists())

    def test_an_install_without_a_release_key_refuses(self):
        self.publish(self.package())
        self.cli.SIGNERS.write_text("# no key yet\n")
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade()
        self.assertIn("no release key", str(cm.exception.code))

    def test_links_and_devices_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        victim = outside / "victim"
        victim.write_text("keep")
        for bad in ([link("iterm-enhancer/d", "../../outside"), ("iterm-enhancer/d/planted", "x", 0o644)],
                    [link("iterm-enhancer/h", str(victim), tarfile.LNKTYPE), ("iterm-enhancer/h", "owned", 0o644)],
                    [link("iterm-enhancer/null", "", tarfile.CHRTYPE)]):
            self.publish(self.package() + bad)
            with self.assertRaises(SystemExit) as cm:
                self.cli.upgrade()
            self.assertIn("not a plain file or folder", str(cm.exception.code))
        self.assertEqual(victim.read_text(), "keep")
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["victim"])
        self.assertFalse(self.marker.exists())

    def test_setuid_bits_are_stripped(self):
        tar = self.root / "p.tar.gz"
        tar.write_bytes(tar_with([("iterm-enhancer/tool", "x", 0o6777)]))
        self.cli.extract(tar, self.root / "x")
        self.assertEqual((self.root / "x/iterm-enhancer/tool").stat().st_mode & 0o7777, 0o755)

    def install_sh(self, signers):
        """Run a copy of install.sh that carries `signers` against the fake release."""
        sh = self.root / "install.sh"
        text = (REPO / "scripts/install.sh").read_text()
        sh.write_text(re.sub(r"(?m)^SIGNERS='.*'$", lambda _: f"SIGNERS='{signers}'", text))
        return subprocess.run(["sh", str(sh)], capture_output=True, text=True,
                              env={**os.environ, "FB_RELEASE_URL": f"file://{self.root}/releases"})

    def test_install_sh_checks_the_signature(self):
        signers = self.cli.SIGNERS.read_text().strip()
        self.publish(self.package(), key=keypair(self.root, "attacker"))
        r = self.install_sh(signers)
        self.assertIn("not signed by the release key", r.stderr)
        self.assertFalse(self.marker.exists())
        self.assertIn("carries no release key", self.install_sh("# no release key yet").stderr)
        self.publish(self.package())
        r = self.install_sh(signers)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marker.read_text().strip(), "install")

    def test_install_sh_and_release_signers_name_the_same_key(self):
        key = lambda text: re.findall(r"ssh-\S+ \S+", text)
        sh = re.search(r"(?m)^SIGNERS='(.*)'$", (REPO / "scripts/install.sh").read_text()).group(1)
        self.assertEqual(key(sh), key((REPO / "release-signers").read_text()))

    def test_install_sh_rejects_a_replayed_signed_release(self):
        build = self.root / "home/.iterm-enhancer/builds/current/BUILD"
        build.parent.mkdir(parents=True)
        build.write_text("v9.1.0\n")
        signers = self.cli.SIGNERS.read_text().strip()
        self.publish(self.package(), release="v9.0.0")
        r = self.install_sh(signers)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("older than the installed v9.1.0", r.stderr)
        self.assertFalse(self.marker.exists())
        self.publish(self.package(), release="v9.2.0")
        r = self.install_sh(signers)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.marker.read_text().strip(), "install")

    def test_an_older_release_is_not_installed_as_the_latest(self):
        build = self.root / "BUILD"
        build.write_text("v9.1.0\n")
        with mock.patch.object(self.cli, "HERE", self.root / "scripts"):
            self.publish(self.package(), release="v9.0.0")
            with self.assertRaises(SystemExit) as cm:
                self.cli.upgrade()
            self.assertIn("older than the installed v9.1.0", str(cm.exception.code))
            self.assertFalse(self.marker.exists())
            (self.root / "releases/download/v9.0.0").mkdir(parents=True)
            for f in self.rel.iterdir():
                (self.root / "releases/download/v9.0.0" / f.name).write_bytes(f.read_bytes())
            with self.assertRaises(SystemExit) as cm:
                self.cli.upgrade("v9.0.0")  # on purpose, by name
            self.assertEqual(cm.exception.code, 0)

    def test_a_release_must_be_the_one_asked_for(self):
        self.publish(self.package(), release="v8.0.0")
        (self.root / "releases/download/v9.9.9").mkdir(parents=True)
        for f in self.rel.iterdir():
            (self.root / "releases/download/v9.9.9" / f.name).write_bytes(f.read_bytes())
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade("v9.9.9")
        self.assertIn("the release says v8.0.0", str(cm.exception.code))
        self.assertFalse(self.marker.exists())

    def test_a_missing_release_says_so(self):
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade("v0.0.0")
        self.assertIn("download failed", str(cm.exception.code))


    # status: connected panels against iTerm2's windows
    def test_panels_against_windows(self):
        self.assertEqual(self.cli.panels_line(2, 2), "panels:   2 connected, 2 iTerm2 windows")
        self.assertEqual(self.cli.panels_line(1, 1), "panels:   1 connected, 1 iTerm2 window")
        self.assertEqual(self.cli.panels_line(14, 2), "panels:   14 connected, 2 iTerm2 windows "
                         "(12 more than windows: iTerm2 keeps panels of earlier registrations until it quits)")

    def test_status_shows_panels_only_once_fbd_knows_the_windows(self):
        health = {"build": "v1.0.0", "bridge_connected": True, "ws_clients": 3, "windows": 2}
        for h, shown in ((health, True), ({**health, "windows": None}, False), (None, False)):
            out = io.StringIO()
            with mock.patch.object(self.cli.live, "health", lambda legacy=False, h=h: h), \
                    mock.patch.object(self.cli, "hosts_list", lambda: None), mock.patch("sys.stdout", out):
                self.cli.status()
            self.assertEqual("panels:   3 connected" in out.getvalue(), shown, out.getvalue())


if __name__ == "__main__":
    unittest.main()
