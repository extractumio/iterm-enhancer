#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Build the fbd agent for remote hosts (AC-39): one script on the Mac and on the runner.

Apple targets build with cargo; Linux targets build static (musl) with cargo-zigbuild and
zig from the `ziglang` wheel, both pinned and kept in ./.toolchain. Output goes to
dist/agents/<agent id>/<platform>/fbd and is reused while the agent id (a hash of fbd's
sources and manifests) is unchanged.

    python3 scripts/agents.py id                       print the agent id
    python3 scripts/agents.py toolchain                install the pinned toolchain (once)
    python3 scripts/agents.py build [PLATFORM ...]     build (default: all four)
    python3 scripts/agents.py build --recorded         the platforms of prepared hosts
"""
import argparse
import hashlib
import json
import os
import platform as host_platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FBD = REPO / "fbd"
# the runner keeps it in its home (FB_TOOLCHAIN): checkout cleans the repository every run
TOOLCHAIN = Path(os.environ.get("FB_TOOLCHAIN") or REPO / ".toolchain")
DIST = REPO / "dist/agents"
ZIG = "0.15.2"            # the ziglang wheel (zig as linker for the Linux targets)
ZIGBUILD = "0.23.4"       # cargo-zigbuild
TARGETS = {"macos-aarch64": "aarch64-apple-darwin", "macos-x86_64": "x86_64-apple-darwin",
           "linux-x86_64": "x86_64-unknown-linux-musl", "linux-aarch64": "aarch64-unknown-linux-musl"}
APP_DIR = Path(os.environ.get("FB_APP_DIR") or Path.home() / ".iterm-filebrowser/state")


class Failed(Exception):
    pass


def agent_id(repo=REPO):
    """12 hex of sha256 over fbd's sources and manifests: equal ids speak the same API."""
    h = hashlib.sha256()
    root = repo / "fbd"
    for f in sorted([*(root / "src").rglob("*.rs"), root / "Cargo.toml", root / "Cargo.lock"]):
        if f.is_file():
            h.update(str(f.relative_to(repo)).encode() + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()[:12]


def run(cmd, env=None, cwd=FBD):
    print("+", " ".join(map(str, cmd)), flush=True)
    r = subprocess.run(cmd, cwd=cwd, env=env)
    if r.returncode != 0:
        raise Failed(f"{cmd[0]} exited {r.returncode}")


def tool_env():
    """PATH with the pinned toolchain first (cargo-zigbuild finds zig through `python3 -m ziglang`)."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(TOOLCHAIN / "bin"), str(TOOLCHAIN / "venv/bin"), env.get("PATH", "")])
    return env


def toolchain(platforms):
    """Rust targets, then zig and cargo-zigbuild for the Linux targets, pinned, in ./.toolchain."""
    targets = [TARGETS[p] for p in platforms]
    if shutil.which("rustup"):
        run(["rustup", "target", "add", *targets])
    if any(p.startswith("linux-") for p in platforms):
        if not (TOOLCHAIN / "venv/bin/python3").exists():
            run([sys.executable, "-m", "venv", str(TOOLCHAIN / "venv")], cwd=REPO)
        run([str(TOOLCHAIN / "venv/bin/python3"), "-m", "pip", "install", "--quiet", f"ziglang=={ZIG}"], cwd=REPO)
        if not (TOOLCHAIN / "bin/cargo-zigbuild").exists():
            run(["cargo", "install", "--locked", "cargo-zigbuild", "--version", ZIGBUILD, "--root", str(TOOLCHAIN)], cwd=REPO)


def native():
    """The platform this machine builds without cross-compiling."""
    system = {"Darwin": "macos", "Linux": "linux"}.get(host_platform.system(), "?")
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "AMD64": "x86_64"}.get(host_platform.machine(), "?")
    return f"{system}-{machine}"


def build(platforms):
    """Build each platform's agent once per agent id; returns {platform: path}."""
    aid = agent_id()
    env = tool_env()
    env.update(FB_AGENT_ID=aid, FB_BUILD=env.get("FB_BUILD") or f"agent-{aid}")
    out = {}
    for p in platforms:
        if p not in TARGETS:
            raise Failed(f"unknown platform {p} (one of {', '.join(TARGETS)})")
        dest = DIST / aid / p / "fbd"
        if not dest.is_file():
            target = TARGETS[p]
            if p.startswith("linux-"):  # the same way on the Mac and on the Linux runner
                if not (TOOLCHAIN / "bin/cargo-zigbuild").exists():
                    raise Failed("the Linux toolchain is missing: run make toolchain")
                run(["cargo", "zigbuild", "--locked", "--release", "--target", target], env=env)
            elif native().startswith("macos-"):
                run(["cargo", "build", "--locked", "--release", "--target", target], env=env)
            else:
                raise Failed(f"{p} builds only on macOS")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(FBD / "target" / target / "release/fbd", dest)
            got = subprocess.run([str(dest), "--agent-id"], capture_output=True, text=True).stdout.strip() if p == native() else aid
            if got != aid:
                raise Failed(f"{dest} says agent id {got}, expected {aid}")
        out[p] = dest
    return out


def recorded():
    """Platforms of the hosts `make agent` prepared."""
    try:
        return sorted({e["platform"] for e in json.loads((APP_DIR / "agents.json").read_text()).values() if e.get("platform")})
    except (OSError, ValueError, KeyError):
        return []


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("id")
    t = sub.add_parser("toolchain")
    t.add_argument("platforms", nargs="*")
    b = sub.add_parser("build")
    b.add_argument("platforms", nargs="*")
    b.add_argument("--recorded", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.cmd == "id":
            return print(agent_id())
        if a.cmd == "toolchain":
            return toolchain(a.platforms or list(TARGETS))
        for plat, path in build(recorded() if a.recorded else (a.platforms or list(TARGETS))).items():
            print(f"{plat}: {path.relative_to(REPO)}")
    except Failed as e:
        if a.cmd == "build" and a.recorded:  # part of make install: the install itself goes on
            return print(f"warning: agents for prepared hosts not built ({e}); they update after `make toolchain` "
                         "and the next `make install`, or with `make agent HOST=…`", file=sys.stderr)
        sys.exit(str(e))


if __name__ == "__main__":
    main()
