# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-40: `make release` publishes nothing until it has signed, never changes a published
release or ships an older version, signs without a prompt and leaves the key out of the
agent. Against a scratch git repository with its own "origin", a private ssh-agent with a
throwaway key, and a fake `gh` that records what it is asked (no network)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "bridge"))

import release  # noqa: E402

FAKE_GH = """#!/bin/sh
printf '%s\n' "$(echo "$*" | tr '\n' ' ')" >> "$FAKE_GH_LOG"
case "$*" in
  "release view"*isDraft*) [ -n "$FAKE_GH_DRAFT" ] || exit 1; echo "$FAKE_GH_DRAFT" ;;
  "release view"*url*) echo "https://example.invalid/releases/$3" ;;
esac
"""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fbrel-", dir="/tmp"))  # short: the agent's socket path
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        origin, self.work = self.tmp / "origin.git", self.tmp / "work"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(origin))
        git(self.tmp, "init", "-q", "-b", "main", str(self.work))
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid"), ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            git(self.work, "config", k, v)
        (self.work / "f").write_text("1")
        git(self.work, "add", "f")
        git(self.work, "commit", "-qm", "one")
        git(self.work, "remote", "add", "origin", str(origin))
        git(self.work, "push", "-q", "origin", "main")
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "gh").write_text(FAKE_GH)
        (bin_ / "gh").chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.log.touch()
        agent = subprocess.run(["ssh-agent", "-s", "-a", str(self.tmp / "agent")], check=True, capture_output=True, text=True).stdout
        pid = next(line.split("=")[1].split(";")[0] for line in agent.splitlines() if line.startswith("SSH_AGENT_PID"))
        self.addCleanup(lambda: subprocess.run(["kill", pid]))
        env = mock.patch.dict(os.environ, {"PATH": f"{bin_}:{os.environ['PATH']}", "FAKE_GH_LOG": str(self.log),
                                           "SSH_AUTH_SOCK": str(self.tmp / "agent")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FAKE_GH_DRAFT", None)
        self.key = self.tmp / "key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(self.key)], check=True)
        signers = self.tmp / "signers"
        signers.write_text(f'{release.cli.NAMESPACE} namespaces="{release.cli.NAMESPACE}" {self.key.with_suffix(".pub").read_text()}')
        out = self.tmp / "out"
        out.mkdir()
        for p in [mock.patch.object(release.package, "REPO", self.work), mock.patch.object(release.package, "OUT", out),
                  mock.patch.object(release.signing_key, "KEY", self.key), mock.patch.object(release.cli, "SIGNERS", signers)]:
            p.start()
            self.addCleanup(p.stop)
        self.out = out

    def calls(self):
        return self.log.read_text().splitlines()

    def refused(self, fn, *args):
        with self.assertRaises(SystemExit) as cm:
            fn(*args)
        return str(cm.exception.code)

    def test_a_new_version_comes_from_main_and_above_every_tag(self):
        release.check_source("v1.0.0")
        git(self.work, "tag", "-a", "v1.2.0", "-m", "v1.2.0")
        git(self.work, "push", "-q", "origin", "v1.2.0")
        self.assertIn("not above the newest release tag v1.2.0", self.refused(release.check_source, "v1.1.0"))
        (self.work / "f").write_text("2")
        self.assertIn("the tree has changes", self.refused(release.check_source, "v1.3.0"))
        git(self.work, "commit", "-qam", "two")
        self.assertIn("HEAD is not origin/main", self.refused(release.check_source, "v1.3.0"))
        self.assertIn("v1.2.0 exists and is not HEAD", self.refused(release.check_source, "v1.2.0"))

    def test_a_published_release_is_never_changed(self):
        os.environ["FAKE_GH_DRAFT"] = "false"
        self.assertIn("already published", self.refused(release.check_unpublished, "v1.0.0"))
        os.environ["FAKE_GH_DRAFT"] = "true"
        self.assertTrue(release.check_unpublished("v1.0.0"), "a draft of an earlier run is completed")
        del os.environ["FAKE_GH_DRAFT"]
        self.assertFalse(release.check_unpublished("v1.0.0"))

    def test_signing_needs_no_prompt_and_leaves_the_key_out_of_the_agent(self):
        sums = self.out / "SHA256SUMS"
        sums.write_text("# release v1.0.0\nabc  x\n")
        release.sign(sums)
        self.assertTrue(release.cli.verify(sums, self.out / "SHA256SUMS.sig"))
        self.assertNotIn("test", subprocess.run(["ssh-add", "-l"], capture_output=True, text=True).stdout)

    def test_a_key_whose_passphrase_is_not_in_the_keychain_fails_without_asking(self):
        subprocess.run(["ssh-keygen", "-q", "-p", "-N", "not-in-any-keychain", "-P", "", "-f", str(self.key)], check=True)
        sums = self.out / "SHA256SUMS"
        sums.write_text("x\n")
        self.assertIn("could not load the release key", self.refused(release.sign, sums))
        self.assertFalse((self.out / "SHA256SUMS.sig").exists())

    def test_publishing_tags_then_fills_a_draft_then_publishes(self):
        for f in (release.package.TARBALL, "install.sh", "SHA256SUMS", "SHA256SUMS.sig"):
            (self.out / f).write_text(f)
        url = release.publish("v1.0.0", draft=False)
        self.assertEqual(git(self.tmp / "origin.git", "rev-parse", "v1.0.0^{commit}"), git(self.work, "rev-parse", "HEAD"))
        verbs = [" ".join(c.split()[:2]) for c in self.calls()]
        self.assertEqual(verbs, ["release create", "release upload", "release edit", "release view"])
        self.assertIn("--draft", self.calls()[0])
        self.assertTrue(url.endswith("/v1.0.0"))
        self.log.write_text("")
        release.publish("v1.0.0", draft=True)  # an earlier run stopped after tagging
        self.assertNotIn("release create", " ".join(self.calls()))


if __name__ == "__main__":
    unittest.main()
