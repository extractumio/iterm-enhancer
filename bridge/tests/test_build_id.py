# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Packaged runtime changes must replace a build; generated files must not."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class BuildIdTest(unittest.TestCase):
    def test_every_packaged_script_and_signer_changes_the_build_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = ["scripts/install.py", "scripts/install_launch.py", "scripts/cli.py",
                      "scripts/iterm-enhancer", "release-signers", "ui/package.json"]
            for rel in inputs:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("original")
            env = {**os.environ, "HOME": str(root / "home"), "FB_APP_DIR": str(root / "state"),
                   "PYTHONPATH": str(REPO / "scripts")}
            code = "import install,sys; from pathlib import Path; print(install.build_id(Path(sys.argv[1])))"

            def build_id():
                return subprocess.run([sys.executable, "-c", code, str(root)], env=env,
                                      check=True, capture_output=True, text=True).stdout.strip()

            before = build_id()
            for rel in inputs:
                with self.subTest(path=rel):
                    (root / rel).write_text("changed")
                    after = build_id()
                    self.assertNotEqual(before, after)
                    before = after
            generated = root / "ui/dist/app.js"
            generated.parent.mkdir(parents=True)
            generated.write_text("generated")
            self.assertEqual(before, build_id())


if __name__ == "__main__":
    unittest.main()
