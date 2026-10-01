#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Publish a release (AC-39, AC-40): build the package as build <tag> on the Mac (it is the
one machine that builds both the macOS and the Linux helpers) and attach it, install.sh
and SHA256SUMS to the GitHub release <tag> (created when missing, from the existing tag).
The owner's runner attaches its own Linux builds to the same release (release.yml).

    python3 scripts/release.py v0.13.0
"""
import subprocess
import sys

import package


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], cwd=package.REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"gh {' '.join(args[:2])}: {r.stderr.strip()}")
    return r


def main(argv):
    if len(argv) != 1 or not argv[0].startswith("v"):
        sys.exit("usage: release.py v<version>")
    tag = argv[0]
    package.main(["--build", tag])
    if not (package.OUT / package.NAME / "agents/linux-x86_64/fbd").is_file():
        sys.exit("the package has no Linux helpers (run make toolchain): not released")
    files = [str(package.OUT / f) for f in (package.TARBALL, "install.sh", "SHA256SUMS")]
    if gh("release", "view", tag, check=False).returncode != 0:
        gh("release", "create", tag, "--verify-tag", "--title", tag, "--notes",
           "Install or upgrade: curl -fsSL https://github.com/extractumio/iterm-extension/releases/latest/download/install.sh | sh")
    gh("release", "upload", tag, *files, "--clobber")
    print(f"{tag}: {', '.join(f.rsplit('/', 1)[-1] for f in files)}")


if __name__ == "__main__":
    main(sys.argv[1:])
