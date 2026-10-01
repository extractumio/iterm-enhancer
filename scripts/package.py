#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Build the Mac package (AC-40): one folder, iterm-filebrowser/, that installs itself.

    BUILD, AGENT_ID                  which build this is; which fbd sources
    agents/<platform>/fbd            fbd for macOS arm64/x86_64 and Linux x86_64/arm64
    bridge/fb_bridge.py, fbbridge/   the iTerm2 AutoLaunch bridge
    scripts/                         the installer and the command's code
    iterm-filebrowser                the command (install, upgrade, rollback, hosts …)

The Mac's own fbd is the macOS agent of its architecture (chosen at install). `--stage`
leaves the folder for `make install`; without it a tarball and SHA256SUMS are written too,
plus install.sh, for a release.

    python3 scripts/package.py [--stage] [--build ID]
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import agents

REPO = agents.REPO
OUT = REPO / "dist/package"
NAME = "iterm-filebrowser"
TARBALL = "iterm-filebrowser-macos.tar.gz"
COPY = ["bridge/fb_bridge.py", "scripts/install.py", "scripts/install_launch.py", "scripts/cli.py",
        "scripts/iterm-filebrowser", "release-signers", "LICENSE", "LICENSE-COMMERCIAL.md", "README.md"]


def build_macos(build):
    """fbd for both Mac architectures, compiled with this build id (the installer checks it)."""
    env = agents.tool_env()
    env.update(FB_BUILD=build, FB_AGENT_ID=agents.agent_id())
    out = {}
    for platform in ("macos-aarch64", "macos-x86_64"):
        target = agents.TARGETS[platform]
        agents.run(["cargo", "build", "--locked", "--release", "--target", target], env=env)
        out[platform] = agents.FBD / "target" / target / "release/fbd"
    return out


def stage(build):
    pkg = OUT / NAME
    shutil.rmtree(pkg, ignore_errors=True)
    (pkg / "scripts").mkdir(parents=True)
    for rel in COPY:
        dest = pkg / rel.replace("scripts/iterm-filebrowser", "iterm-filebrowser")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dest)
    shutil.copytree(REPO / "bridge/fbbridge", pkg / "bridge/fbbridge", ignore=shutil.ignore_patterns("__pycache__"))
    binaries = build_macos(build)
    try:
        binaries.update(agents.build(["linux-x86_64", "linux-aarch64"]))
    except agents.Failed as e:  # a developer without the cross toolchain: Mac only, said loudly
        print(f"warning: no Linux helpers in this package ({e}); Linux hosts cannot be enabled. Run make toolchain.",
              file=sys.stderr)
    for platform, path in binaries.items():
        (pkg / "agents" / platform).mkdir(parents=True)
        shutil.copy2(path, pkg / "agents" / platform / "fbd")
    (pkg / "BUILD").write_text(build + "\n")
    (pkg / "AGENT_ID").write_text(agents.agent_id() + "\n")
    os.chmod(pkg / "iterm-filebrowser", 0o755)
    return pkg


def archive(pkg):
    tar = OUT / TARBALL
    with tarfile.open(tar, "w:gz") as t:
        t.add(pkg, arcname=NAME)
    shutil.copy2(REPO / "scripts/install.sh", OUT / "install.sh")
    # the signed list names its release, so an older one cannot be served as the latest
    sums = f"# release {(pkg / 'BUILD').read_text().strip()}\n" + "".join(
        f"{hashlib.sha256((OUT / f).read_bytes()).hexdigest()}  {f}\n" for f in (TARBALL, "install.sh"))
    (OUT / "SHA256SUMS").write_text(sums)
    return [tar, OUT / "install.sh", OUT / "SHA256SUMS"]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--stage", action="store_true", help="only the folder (make install)")
    p.add_argument("--build", default=os.environ.get("FB_BUILD"))
    a = p.parse_args(argv)
    if not a.build:
        sys.exit("no build id: set FB_BUILD or --build")
    try:
        pkg = stage(a.build)
        print(pkg.relative_to(REPO))
        if not a.stage:
            for f in archive(pkg):
                print(f.relative_to(REPO))
    except agents.Failed as e:
        sys.exit(str(e))
    return pkg


if __name__ == "__main__":
    main()
