#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Install, upgrade, roll back and uninstall the iTerm2 File Browser (AC-33, AC-35).

Each build lives in its own folder, <lib>/<build>/{fbd, bridge/fbbridge, BUILD}; the links
`current` and `previous` name the live and the last build, and each is switched with one
rename. A running bridge keeps its own build (it resolved `current` when it started), so
a switch takes effect when the new bridge takes over, and a build that does not come up
healthy is switched back.

    python3 scripts/install.py id                    print the build id of the checkout
    python3 scripts/install.py install --build ID    install or upgrade (make install / upgrade)
    python3 scripts/install.py rollback              back to `previous` (make rollback)
    python3 scripts/install.py uninstall             remove builds, links, scripts (keeps state)
"""
import argparse
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import agents
import install_launch as live

REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()
LIB = Path(os.environ.get("FB_LIB_DIR") or HOME / ".local/lib/iterm-filebrowser")
BIN = Path(os.environ.get("FB_BIN_DIR") or HOME / ".local/bin")
AUTOLAUNCH = Path(os.environ.get("FB_AUTOLAUNCH_DIR") or HOME / "Library/Application Support/iTerm2/Scripts/AutoLaunch")
APP_DIR = live.APP_DIR
PROFILE = HOME / "Library/Application Support/iTerm2/DynamicProfiles/iterm-filebrowser.json"
SOURCES = ["bridge/fb_bridge.py", "bridge/fbbridge", "fbd/src", "fbd/Cargo.toml", "fbd/Cargo.lock",
           "ui/src", "ui/public", "ui/build.mjs", "ui/package-lock.json"]


class Failed(Exception):
    """A step failed; the message is for the user."""


def say(msg):
    print(msg, flush=True)


# ── build id ────────────────────────────────────────────────────────────────

def build_id(repo=REPO):
    """`<commit>-<hash of every source that goes into a build>`: a change anywhere (the
    bridge included) gives a new id; the same sources give the same id."""
    h = hashlib.sha256()
    for rel in SOURCES:
        root = repo / rel
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        for f in files:
            h.update(str(f.relative_to(repo)).encode() + b"\0" + f.read_bytes() + b"\0")
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return f"{r.stdout.strip() or 'nogit'}-{h.hexdigest()[:8]}"


# ── layout ──────────────────────────────────────────────────────────────────

def target(name):
    """The build a link names, or None."""
    link = LIB / name
    return os.readlink(link) if link.is_symlink() else None


def swap_link(link, dest):
    """Point `link` at `dest` with one rename: readers see the old or the new one, never none."""
    tmp = link.with_name(f".{link.name}.new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(dest)
    tmp.replace(link)


def relink(name, build):
    swap_link(LIB / name, build)


def unlink(name):
    if (LIB / name).is_symlink():
        (LIB / name).unlink()


def copy_build(build, fbd):
    """Copy the build into <lib>/<build> through a hidden folder renamed at the end, so a
    half-copied build is never linked."""
    dest = LIB / build
    if dest.is_dir():
        return
    tmp = LIB / f".{build}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "bridge").mkdir(parents=True)
    shutil.copy2(fbd, tmp / "fbd")
    os.chmod(tmp / "fbd", 0o755)
    shutil.copytree(REPO / "bridge/fbbridge", tmp / "bridge/fbbridge", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "BUILD").write_text(build + "\n")
    # the agents of prepared hosts, so the bridge brings their agents up to this build (AC-38)
    aid = agents.agent_id()
    (tmp / "AGENT_ID").write_text(aid + "\n")
    for plat in agents.recorded():
        built = agents.DIST / aid / plat / "fbd"
        if built.is_file():
            (tmp / "agents" / plat).mkdir(parents=True)
            shutil.copy2(built, tmp / "agents" / plat / "fbd")
    tmp.rename(dest)


def place_entry_points():
    """`fbd` on PATH follows `current`; iTerm2's AutoLaunch runs the bridge entry."""
    BIN.mkdir(parents=True, exist_ok=True)
    swap_link(BIN / "fbd", LIB / "current/fbd")  # also replaces the file of an unversioned install
    AUTOLAUNCH.mkdir(parents=True, exist_ok=True)
    tmp = AUTOLAUNCH / ".fb_bridge.py.new"
    shutil.copy2(REPO / "bridge/fb_bridge.py", tmp)
    tmp.replace(AUTOLAUNCH / "fb_bridge.py")


def drop_unversioned():
    """Installs before versioned builds kept the bridge package in the app folder."""
    shutil.rmtree(APP_DIR / "bridge", ignore_errors=True)


def prune():
    """Keep `current` and `previous`; drop older builds and the unversioned bridge copy."""
    keep = {target("current"), target("previous")}
    for d in LIB.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)
    drop_unversioned()


def lock():
    """One install at a time; the lock goes with the process."""
    LIB.mkdir(parents=True, exist_ok=True)
    APP_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(APP_DIR / "install.lock", os.O_RDWR | os.O_CREAT, 0o600)  # survives uninstall's rmtree
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise Failed("Another install is running")
    return fd


# ── going live ──────────────────────────────────────────────────────────────

def go_live(build):
    """Launch the bridge of `build` and wait for it. True: live; False: iTerm2 is not running
    (it takes effect at the next start). Raises LaunchError or Failed."""
    if not live.iterm_running():
        return False
    live.launch()
    if not live.wait_healthy(build):
        raise Failed(f"build {build} did not report healthy within 30 s ({live.describe_health()})")
    return True


def install(build, fbd):
    if not fbd.is_file():
        raise Failed(f"{fbd} not found: run make install (it builds first)")
    got = subprocess.run([str(fbd), "--version"], capture_output=True, text=True).stdout.strip()
    if got != build:
        raise Failed(f"{fbd} is build {got or 'unknown'}, expected {build}: rebuild with make install")
    old = target("current")
    if old == build:
        if live.iterm_running() and live.runs(live.health(), build):
            return say(f"{build} is already installed and running")
    copy_build(build, fbd)
    if old and old != build:
        relink("previous", old)
    relink("current", build)
    place_entry_points()
    try:
        is_live = go_live(build)
    except live.LaunchError as e:
        raise Failed(f"Installed {build}, but iTerm2 did not start it: {e}")
    except Failed as e:
        if not old or old == build:
            raise Failed(f"Install of {build} failed ({e}); see {live.LOG_DIR}")
        relink("current", old)
        unlink("previous")
        try:
            go_live(old)
        except (Failed, live.LaunchError) as again:
            raise Failed(f"Upgrade to {build} failed ({e}); switched back to {old}, which did not come up either ({again})")
        raise Failed(f"Upgrade to {build} failed ({e}); rolled back to {old} — see {live.LOG_DIR}")
    if not is_live:
        return say(f"Installed {build}; takes effect when iTerm2 starts")
    prune()
    say(f"Upgraded {old} → {build}" if old and old != build else f"Installed {build}; the Files panel is live (View → Toolbelt → Files)")


def rollback():
    prev, cur = target("previous"), target("current")
    if not prev or not (LIB / prev).is_dir():
        raise Failed("No previous build to roll back to")
    relink("current", prev)
    if cur:
        relink("previous", cur)
    else:
        unlink("previous")
    try:
        is_live = go_live(prev)
    except (Failed, live.LaunchError) as e:
        raise Failed(f"Rolled back to {prev}, but it did not come up: {e}")
    say(f"Rolled back {cur} → {prev}" + ("" if is_live else "; takes effect when iTerm2 starts"))


def uninstall():
    subprocess.run(["pkill", "-f", "AutoLaunch/fb_bridge.py"], capture_output=True)
    subprocess.run(["pkill", "-x", "fbd"], capture_output=True)
    shutil.rmtree(LIB, ignore_errors=True)
    for p in (BIN / "fbd", AUTOLAUNCH / "fb_bridge.py", PROFILE):
        if p.is_symlink() or p.exists():
            p.unlink()
    drop_unversioned()
    say("Removed the File Browser builds, fbd and the AutoLaunch bridge. Token and workspaces kept in "
        "~/Library/Application Support/iterm-filebrowser")
    say("To remove the Files entry from iTerm2's Toolbelt menu: quit iTerm2, then run 'make clean-registrations'")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("id")
    i = sub.add_parser("install")
    i.add_argument("--build", required=True)
    i.add_argument("--fbd", type=Path, default=REPO / "fbd/target/release/fbd")
    sub.add_parser("rollback")
    sub.add_parser("uninstall")
    a = p.parse_args(argv)
    if a.cmd == "id":
        return print(build_id())
    if os.geteuid() == 0:
        sys.exit("Run as your user, not root: the install lives in your home folder")
    try:
        fd = lock()
        try:
            {"install": lambda: install(a.build, a.fbd), "rollback": rollback, "uninstall": uninstall}[a.cmd]()
        finally:
            os.close(fd)
    except Failed as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
