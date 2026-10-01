# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-40: `iterm-filebrowser upgrade` installs a release only when its checksum matches,
from a fake release served as local files (no network)."""
import hashlib
import importlib
import io
import os
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
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text, mode in files:
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.marker = self.root / "installed"
        rel = self.root / "releases/latest/download"
        rel.mkdir(parents=True)
        self.rel = rel
        p = mock.patch.dict(os.environ, {"FB_RELEASE_URL": f"file://{self.root}/releases", "FB_APP_DIR": str(self.root / "app")})
        p.start()
        self.addCleanup(p.stop)
        import cli
        self.cli = importlib.reload(cli)

    def publish(self, files, checksum=None):
        data = tar_with(files)
        (self.rel / "iterm-filebrowser-macos.tar.gz").write_bytes(data)
        digest = checksum or hashlib.sha256(data).hexdigest()
        (self.rel / "SHA256SUMS").write_text(f"{digest}  iterm-filebrowser-macos.tar.gz\n")

    def package(self):
        return [("iterm-filebrowser/iterm-filebrowser", f"#!/bin/sh\necho \"$1\" > {self.marker}\n", 0o755),
                ("iterm-filebrowser/BUILD", "v9\n", 0o644)]

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

    def test_a_missing_release_says_so(self):
        with self.assertRaises(SystemExit) as cm:
            self.cli.upgrade("v0.0.0")
        self.assertIn("download failed", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
