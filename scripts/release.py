#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Publish a release (AC-39, AC-40) from the Mac, the one machine that builds both the macOS
and the Linux helpers: only from a clean tree at the tag's commit, with the tag on main;
dependencies exactly as locked (npm ci, cargo --locked); SHA256SUMS signed with the
maintainer's release key (FB_RELEASE_KEY, see signing_key.py) and checked against
release-signers before anything is uploaded. Attaches the package, install.sh, SHA256SUMS
and SHA256SUMS.sig to the GitHub release <tag> (created when missing, from the tag).

    python3 scripts/release.py v0.13.0
"""
import subprocess
import sys

import cli
import package
import signing_key


def run(*cmd, cwd=package.REPO):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"{' '.join(cmd[:3])}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], cwd=package.REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:2])}: {r.stderr.strip()}")
    return r


def check_source(tag):
    """What users run is exactly the public tag: a clean tree at its commit, on main."""
    run("git", "fetch", "--quiet", "origin", "main")  # judge against main as it is now
    if run("git", "status", "--porcelain"):
        sys.exit("the tree has changes: commit or stash them (a release is built from the tag only)")
    if run("git", "rev-parse", f"{tag}^{{commit}}") != run("git", "rev-parse", "HEAD"):
        sys.exit(f"HEAD is not {tag}: check out the tag first")
    if subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=package.REPO).returncode != 0:
        sys.exit(f"{tag} is not on origin/main (git fetch, or push main first)")


def sign(sums):
    """Sign SHA256SUMS, then check the signature as installs will."""
    if not signing_key.KEY.is_file():
        sys.exit(f"no release key at {signing_key.KEY}: run make signing-key once")
    run("ssh-keygen", "-Y", "sign", "-q", "-f", str(signing_key.KEY), "-n", cli.NAMESPACE, str(sums))
    if not cli.verify(sums, sums.with_name("SHA256SUMS.sig")):
        sys.exit("the signature does not match release-signers: commit the key's public half (make signing-key)")


def main(argv):
    if len(argv) != 1 or not argv[0].startswith("v"):
        sys.exit("usage: release.py v<version>")
    tag = argv[0]
    check_source(tag)
    run("npm", "ci", "--ignore-scripts", "--no-fund", "--no-audit", cwd=package.REPO / "ui")
    run("npm", "run", "-s", "build", cwd=package.REPO / "ui")
    package.main(["--build", tag])
    if not (package.OUT / package.NAME / "agents/linux-x86_64/fbd").is_file():
        sys.exit("the package has no Linux helpers (run make toolchain): not released")
    sign(package.OUT / "SHA256SUMS")
    files = [str(package.OUT / f) for f in (package.TARBALL, "install.sh", "SHA256SUMS", "SHA256SUMS.sig")]
    if gh("release", "view", tag, check=False).returncode != 0:
        gh("release", "create", tag, "--verify-tag", "--title", tag, "--notes",
           "Install or upgrade: curl -fsSL https://github.com/extractumio/iterm-extension/releases/latest/download/install.sh | sh")
    gh("release", "upload", tag, *files, "--clobber")
    print(f"{tag}: {', '.join(f.rsplit('/', 1)[-1] for f in files)}")


if __name__ == "__main__":
    main(sys.argv[1:])
