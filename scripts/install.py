#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Install, upgrade, roll back and uninstall the iTerm2 File Browser (AC-33, AC-35, AC-40).

Everything lives under one root, ~/.iterm-filebrowser/ (AC-40): bin/, builds/, logs/,
state/. Each build lives in its own folder, builds/<build>/ (a copy of its package: BUILD, agents/,
bridge/, scripts/, the iterm-filebrowser command, and fbd linked to the macOS agent of this
Mac); the links `current` and `previous` name the live and the last build, and each is
switched with one rename. A running bridge keeps its own build (it resolved `current` when
it started), so a switch takes effect when the new bridge takes over, and a build that
does not come up healthy is switched back. Users run it through `iterm-filebrowser`.

    python3 scripts/install.py id                     the build id of a checkout's sources
    python3 scripts/install.py install [--from DIR]   install or upgrade to a package
    python3 scripts/install.py rollback               back to `previous`
    python3 scripts/install.py uninstall              remove builds, links, scripts (keeps state)
"""
import argparse
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import platform as host_platform

import install_launch as live

# the folder this installer came in: a package (BUILD, agents/, bridge/, scripts/), or the
# checkout, where only `id` is meaningful (make install stages a package first)
REPO = Path(__file__).resolve().parent.parent
HOME = Path.home()
ROOT = live.ROOT
LIB = ROOT / "builds"
BIN = ROOT / "bin"
APP_DIR, LOG_DIR = live.APP_DIR, live.LOG_DIR
AUTOLAUNCH = HOME / "Library/Application Support/iTerm2/Scripts/AutoLaunch"
PROFILE = HOME / "Library/Application Support/iTerm2/DynamicProfiles/iterm-filebrowser.json"
# the layout before one root: moved by move_old_layout, removed by drop_old_layout
OLD_LIB = HOME / ".local/lib/iterm-filebrowser"
OLD_BIN = HOME / ".local/bin"
OLD_DIRS = {HOME / "Library/Application Support/iterm-filebrowser": APP_DIR, HOME / "Library/Logs/iterm-filebrowser": LOG_DIR}
OLD_MARK = ".old-layout"  # in a build copied from the earlier layout: it uses the old folders
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


def mac_platform():
    """The Mac's own architecture: under Rosetta, Python would say x86_64 on Apple Silicon."""
    r = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True)
    return "macos-aarch64" if r.stdout.strip() == "1" or host_platform.machine() == "arm64" else "macos-x86_64"


def copy_build(build, pkg):
    """Copy the package into <lib>/<build> through a hidden folder renamed at the end, so a
    half-copied build is never linked. The Mac's fbd is the macOS agent of this Mac's
    architecture; the others stay for remote hosts (AC-38)."""
    dest = LIB / build
    if dest.is_dir():
        return
    tmp = LIB / f".{build}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(pkg, tmp, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "fbd").symlink_to(f"agents/{mac_platform()}/fbd")
    tmp.rename(dest)


def place_entry_points(build):
    """`fbd` follows `current`; iTerm2's AutoLaunch runs the bridge entry."""
    BIN.mkdir(parents=True, exist_ok=True)
    swap_link(BIN / "fbd", LIB / "current/fbd")  # also replaces the file of an unversioned install
    # the command of the newest installed build, not of `current`: after a rollback into an
    # older build it still knows this layout (a rollback never moves it)
    swap_link(BIN / "iterm-filebrowser", LIB / build / "iterm-filebrowser")
    drop_old_commands()
    AUTOLAUNCH.mkdir(parents=True, exist_ok=True)
    tmp = AUTOLAUNCH / ".fb_bridge.py.new"
    shutil.copy2(LIB / "current/bridge/fb_bridge.py", tmp)
    tmp.replace(AUTOLAUNCH / "fb_bridge.py")


def move_dir(old, new):
    """Move the folder `old` into `new` and leave a link at `old`. A missing or empty `new`
    takes the whole folder with one rename (open files and locks stay valid); otherwise the
    entries move one by one, and a name in both is refused, never overwritten. A log the
    running bridge recreates meanwhile is merged again (AC-40)."""
    for _ in range(5):
        if not old.is_dir() or old.is_symlink():
            return
        if new.is_dir() and not new.is_symlink() and not any(new.iterdir()):
            new.rmdir()
        if not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        else:
            for p in old.iterdir():
                if not (new / p.name).exists():
                    p.rename(new / p.name)
                elif new == LOG_DIR and p.is_file():  # lines logged while moving
                    with open(new / p.name, "ab") as f:
                        f.write(p.read_bytes())
                    p.unlink()
                else:
                    raise Failed(f"{p} and {new / p.name} both exist: keep the one you want "
                                 f"(the old one holds what iTerm2 uses now), remove the other, and run again")
            try:
                old.rmdir()
            except OSError:
                continue  # written to while moving: merge again
        try:
            return old.symlink_to(new)
        except FileExistsError:
            continue
    raise Failed(f"{old} keeps changing while it is moved to {new}: quit iTerm2 and run again")


def move_old_layout():
    """Move state and logs from the earlier layout (the token, so the registered Toolbelt
    URL, stays valid) and leave links in the old places for builds that still use them;
    copy the old `current` and `previous` builds into builds/, marked, and link them the same,
    so an install that follows keeps the old build as `previous` (AC-40)."""
    try:
        for old, new in OLD_DIRS.items():
            if new != APP_DIR or not os.environ.get("FB_APP_DIR"):
                move_dir(old, new)
        if target("current"):
            return
        for name in ("previous", "current"):  # `current` last: it is what says "moved"
            link = OLD_LIB / name
            build = os.readlink(link) if link.is_symlink() else ""
            if not build or "/" in build or not (OLD_LIB / build).is_dir():
                continue
            if not (LIB / build).is_dir():
                tmp = LIB / f".{build}.tmp"
                shutil.rmtree(tmp, ignore_errors=True)
                shutil.copytree(OLD_LIB / build, tmp, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
                (tmp / OLD_MARK).touch()
                tmp.rename(LIB / build)
            relink(name, build)
    except OSError as e:
        raise Failed(f"Could not move the earlier install into {ROOT}: {e}; nothing was deleted, run again")


def drop_old_commands():
    """Our commands in ~/.local/bin from the earlier layout: links into its builds, or the
    copied fbd of installs before Stage 7 (an fbd that is not ours stays)."""
    for name in ("fbd", "iterm-filebrowser"):
        p = OLD_BIN / name
        if p.is_symlink():
            ours = os.readlink(p).startswith(str(OLD_LIB) + "/")
        else:
            ours = name == "fbd" and p.is_file() and b"FB_APP_DIR" in p.read_bytes()
        if ours:
            p.unlink()


def drop_old_layout(kept):
    """Remove the earlier layout's builds; the links at the old state and log folders go
    once no build in `kept` came from it."""
    if OLD_LIB.is_dir() and not OLD_LIB.is_symlink():
        shutil.rmtree(OLD_LIB, ignore_errors=True)
    if not any(b and (LIB / b / OLD_MARK).exists() for b in kept):
        for old, new in OLD_DIRS.items():
            if old.is_symlink() and old.resolve() == new.resolve():
                old.unlink()


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
    drop_old_layout(keep)


def lock():
    """One install at a time; the lock goes with the process. It lives in the root, beside
    what uninstall removes and outside state/, which the earlier layout's move creates."""
    LIB.mkdir(parents=True, exist_ok=True)
    fd = os.open(ROOT / "install.lock", os.O_RDWR | os.O_CREAT, 0o600)
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


def install(pkg=REPO):
    """Install (or upgrade to) the package in folder `pkg`."""
    if not (pkg / "BUILD").is_file():
        raise Failed(f"{pkg} is not a package (no BUILD): from a checkout, run make install")
    build = (pkg / "BUILD").read_text().strip()
    fbd = pkg / "agents" / mac_platform() / "fbd"
    if not fbd.is_file():
        raise Failed(f"the package has no fbd for {mac_platform()}")
    got = subprocess.run([str(fbd), "--version"], capture_output=True, text=True).stdout.strip()
    if got != build:
        raise Failed(f"{fbd} is build {got or 'unknown'}, expected {build}")
    move_old_layout()  # only once the package is known to be good
    old = target("current")
    if old == build:
        if live.iterm_running() and live.runs(live.health(), build):
            return say(f"{build} is already installed and running")
    copy_build(build, pkg)
    if old and old != build:
        relink("previous", old)
    relink("current", build)
    place_entry_points(build)
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


def path_tip():
    """The command moved out of ~/.local/bin (AC-40): say how to reach it, once per install."""
    if str(BIN) not in os.environ.get("PATH", "").split(":"):
        say(f"Tip: add {BIN} to your PATH to use the iterm-filebrowser command:\n"
            f"  echo 'export PATH=\"$HOME/.iterm-filebrowser/bin:$PATH\"' >> ~/.zshrc")


def rollback():
    move_old_layout()
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
    move_old_layout()  # so the state it keeps is where it says
    subprocess.run(["pkill", "-f", "AutoLaunch/fb_bridge.py"], capture_output=True)
    subprocess.run(["pkill", "-x", "fbd"], capture_output=True)
    shutil.rmtree(LIB, ignore_errors=True)
    for p in (BIN / "fbd", BIN / "iterm-filebrowser", AUTOLAUNCH / "fb_bridge.py", PROFILE):
        if p.is_symlink() or p.exists():
            p.unlink()
    if BIN.is_dir() and not any(BIN.iterdir()):
        BIN.rmdir()
    drop_unversioned()
    drop_old_layout(kept=())
    say(f"Removed the File Browser builds, its commands and the AutoLaunch bridge. Token, workspaces and logs kept in {ROOT}")
    say("To remove the Files entry from iTerm2's Toolbelt menu: quit iTerm2, then run 'make clean-registrations'")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("id")
    i = sub.add_parser("install")
    i.add_argument("--from", dest="pkg", type=Path, default=REPO, help="the package folder (default: the one this script is in)")
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
            {"install": lambda: install(a.pkg.resolve()), "rollback": rollback, "uninstall": uninstall}[a.cmd]()
            if a.cmd == "install":
                path_tip()
        finally:
            os.close(fd)
    except Failed as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
