#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Install, upgrade, roll back and uninstall iterm-enhancer (AC-33, AC-35, AC-40).

Everything lives under one root, ~/.iterm-enhancer/ (AC-40): bin/, builds/, logs/,
state/. Each build lives in its own folder, builds/<build>/ (a copy of its package: BUILD, agents/,
bridge/, scripts/, the iterm-enhancer command, and fbd linked to the macOS agent of this
Mac); the links `current` and `previous` name the live and the last build, and each is
switched with one rename. A running bridge keeps its own build (it resolved `current` when
it started), so a switch takes effect when the new bridge takes over, and a build that
does not come up healthy is switched back. Users run it through `iterm-enhancer`.

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
sys.path.insert(0, str(REPO / "bridge"))
from fbbridge import setup_state  # noqa: E402
HOME = Path.home()
ROOT = live.ROOT
LIB = ROOT / "builds"
BIN = ROOT / "bin"
APP_DIR, LOG_DIR = live.APP_DIR, live.LOG_DIR
AUTOLAUNCH = HOME / "Library/Application Support/iTerm2/Scripts/AutoLaunch"
PROFILE = HOME / "Library/Application Support/iTerm2/DynamicProfiles/iterm-enhancer.json"
SOURCES = ["bridge/fb_bridge.py", "bridge/fbbridge", "fbd/src", "fbd/Cargo.toml", "fbd/Cargo.lock",
           "ui/src", "ui/public", "ui/build.mjs", "ui/package.json", "ui/package-lock.json",
           "scripts/install.py", "scripts/install_launch.py", "scripts/cli.py", "scripts/iterm-enhancer", "release-signers"]


class Failed(Exception):
    """A step failed; the message is for the user."""


def say(msg):
    print(msg, flush=True)


def color():
    """Colors only for a person at a terminal that takes them (NO_COLOR: no-color.org)."""
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM", "dumb") != "dumb"


def done(msg):
    """A success line: with a green check where colors work, the bare text elsewhere (logs, scripts)."""
    say(f"\033[32m✓\033[0m {msg}" if color() else msg)


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
    swap_link(BIN / "fbd", LIB / "current/fbd")
    # the command of the newest installed build, not of `current`: after a rollback into an
    # older build it still knows this layout (a rollback never moves it)
    swap_link(BIN / "iterm-enhancer", LIB / build / "iterm-enhancer")
    AUTOLAUNCH.mkdir(parents=True, exist_ok=True)
    tmp = AUTOLAUNCH / ".fb_bridge.py.new"
    shutil.copy2(LIB / "current/bridge/fb_bridge.py", tmp)
    tmp.replace(AUTOLAUNCH / "fb_bridge.py")


def prune():
    """Keep `current` and `previous`; drop older builds."""
    keep = {target("current"), target("previous")}
    for d in LIB.iterdir():
        if d.is_dir() and not d.is_symlink() and not d.name.startswith(".") and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)


def lock():
    """One install at a time; the lock goes with the process. It lives in the root, beside
    what uninstall removes. The root is private (0700): its state and logs are the user's
    alone (AC-07)."""
    LIB.mkdir(parents=True, exist_ok=True)
    ROOT.chmod(0o700)
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


def report_settings(request_id, is_live):
    if not is_live:
        return
    try:
        result = setup_state.wait_result(APP_DIR, request_id)
    except (ValueError, OSError):
        return say("iTerm2 setup incomplete: unreadable setup result; run the installer again")
    if result is None:
        return say("iTerm2 setup is pending; the Files panel will report changes when setup completes")
    message = setup_state.notice(result)
    if message:
        say(message["message"])


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
    request_id = setup_state.request(APP_DIR)
    old = target("current")
    if old == build:
        if live.iterm_running() and live.runs(live.health(), build):
            report_settings(request_id, True)
            return done(f"{build} is already installed and running")
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
        return done(f"Installed {build}; takes effect when iTerm2 starts")
    report_settings(request_id, True)
    prune()
    done(f"Upgraded {old} → {build}" if old and old != build else f"Installed {build}; the Files panel is live (View → Toolbelt → Files)")


STARTUP_FILES = (".zshenv", ".zprofile", ".zshrc", ".bash_profile", ".bashrc", ".profile", ".config/fish/config.fish")


def path_ready():
    """Is the command reachable already: on this shell's PATH, or set up in a startup file (a
    shell opened before that edit has not read it, and must not be told to do it again)."""
    if str(BIN) in os.environ.get("PATH", "").split(":"):
        return True
    zdot = Path(os.environ["ZDOTDIR"]) if os.environ.get("ZDOTDIR") else None
    files = [HOME / f for f in STARTUP_FILES] + ([zdot / ".zshenv", zdot / ".zprofile", zdot / ".zshrc"] if zdot else [])
    for f in files:
        try:
            with open(f, errors="ignore") as lines:
                if any(".iterm-enhancer/bin" in line for line in lines if not line.lstrip().startswith("#")):
                    return True
        except OSError:
            pass
    return False


def path_tip():
    """The command lives in the root's bin/ (AC-40): after the result, a blank line and a
    cornflower-blue tip on how to reach it, unless the user did that already. A fixed
    256-color shade, not a palette color: "bright black" is nearly the background in some
    profiles (Solarized-like dark themes)."""
    if path_ready():
        return
    tip = (f"Tip: add {BIN} to your PATH to use the iterm-enhancer command:\n"
           f"  echo 'export PATH=\"$HOME/.iterm-enhancer/bin:$PATH\"' >> ~/.zshrc")
    say("")
    say(f"\033[38;5;69m{tip}\033[0m" if color() else tip)


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
    for p in (BIN / "fbd", BIN / "iterm-enhancer", AUTOLAUNCH / "fb_bridge.py", PROFILE):
        if p.is_symlink() or p.exists():
            p.unlink()
    if BIN.is_dir() and not any(BIN.iterdir()):
        BIN.rmdir()
    say(f"Removed iterm-enhancer builds, its commands and the AutoLaunch bridge. Token, workspaces and logs kept in {ROOT}")
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
    os.umask(0o077)
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
