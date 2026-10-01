# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-39: the workflows run on the owner's runner only, never for pull requests (this
repository is public: a fork must not run code on the runner), read-only, with actions
pinned to a commit and listed in .github/actions-allowlist.json. Plain text checks, no
YAML library."""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO / ".github/workflows").glob("*.yml"))
ALLOWED = set(json.loads((REPO / ".github/actions-allowlist.json").read_text())["actions"])


class WorkflowTest(unittest.TestCase):
    def test_there_are_workflows(self):
        self.assertTrue(WORKFLOWS)

    def test_owner_runner_only(self):
        for wf in WORKFLOWS:
            for line in re.findall(r"^\s*runs-on:\s*(.+)$", wf.read_text(), re.M):
                self.assertEqual(line.strip(), "[self-hosted, linux, x64]", f"{wf.name}: runs-on {line}")

    def test_no_triggers_a_fork_can_reach(self):
        for wf in WORKFLOWS:
            self.assertNotRegex(wf.read_text(), r"\b(pull_request(_target|_review(_comment)?)?|workflow_run|issue_comment)\b", wf.name)

    def test_actions_pinned_and_allowed(self):
        for wf in WORKFLOWS:
            for use in re.findall(r"^\s*-?\s*uses:\s*(\S+)", wf.read_text(), re.M):
                self.assertRegex(use, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$", f"{wf.name}: {use} is not pinned to a commit")
                self.assertIn(use, ALLOWED, f"{wf.name}: {use} is not in .github/actions-allowlist.json")

    def test_no_github_storage(self):
        for wf in WORKFLOWS:
            text = wf.read_text()
            for banned in ("actions/cache", "upload-artifact", "download-artifact", "container:"):
                self.assertNotIn(banned, text, f"{wf.name}: {banned}")

    def test_the_runner_never_writes(self):
        """Code on the runner (a build script, a dependency) must not be able to change a
        release: releases are published from the maintainer's Mac only."""
        for wf in WORKFLOWS:
            text = wf.read_text()
            self.assertRegex(text, r"(?m)^permissions:\s*(\{\}|\n\s+contents: read)", f"{wf.name}: top-level permissions")
            self.assertNotRegex(text, r":\s*write\b", f"{wf.name}: a write permission")
            for banned in ("GH_TOKEN", "GITHUB_TOKEN", "github.token", "secrets."):
                self.assertNotIn(banned, text, f"{wf.name}: {banned}")

    def test_locked_builds(self):
        for wf in WORKFLOWS:
            for cmd in re.findall(r"cargo (?:test|build)[^\n)]*", wf.read_text()):
                self.assertIn("--locked", cmd, f"{wf.name}: {cmd}")


if __name__ == "__main__":
    unittest.main()
