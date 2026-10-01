#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Attach agents to the GitHub release of a tag (AC-39). The owner's runner attaches the
Linux agents (release.yml); `make release TAG=…` on the Mac the macOS ones, which build
only on macOS. Each side adds its own checksum file, SHA256SUMS-<os>.

    python3 scripts/release.py <tag> [PLATFORM ...]    (default: both macOS platforms)
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import agents

REPO = agents.REPO


def gh(*args, check=True):
    r = subprocess.run(["gh", *args], cwd=REPO, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise agents.Failed(f"gh {args[0]} {args[1] if len(args) > 1 else ''}: {r.stderr.strip()}")
    return r


def main(argv):
    if not argv or not argv[0].startswith("v"):
        sys.exit("usage: release.py v<version> [PLATFORM ...]")
    tag, platforms = argv[0], argv[1:] or ["macos-aarch64", "macos-x86_64"]
    os.environ["FB_BUILD"] = tag
    try:
        built = agents.build(platforms)
        out = REPO / "dist/release" / tag
        out.mkdir(parents=True, exist_ok=True)
        files = []
        for plat, path in built.items():
            dest = out / f"fbd-agent-{tag}-{plat}"
            shutil.copy2(path, dest)
            files.append(dest)
        system = {p.split("-")[0] for p in platforms}
        sums = out / f"SHA256SUMS-{'-'.join(sorted(system))}"
        sums.write_text("".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in files))
        if gh("release", "view", tag, check=False).returncode != 0:
            gh("release", "create", tag, "--verify-tag", "--title", tag,
               "--notes", f"Agents for remote hosts (fbd agent id {agents.agent_id()}). See README: Remote hosts.")
        gh("release", "upload", tag, *map(str, files), str(sums), "--clobber")
        print(f"{tag}: {', '.join(f.name for f in files)}, {sums.name}")
    except agents.Failed as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main(sys.argv[1:])
