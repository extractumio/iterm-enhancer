# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Launching the installed bridge through iTerm2 and waiting until the new build answers
(AC-33). Kept apart from the file work in install.py; tests replace these functions."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
from fbbridge import unixhttp  # noqa: E402  (no paths: it is safe to import under a test's home)

# read here, not imported from fbbridge.common: tests reload this module under a temporary
# home, and an already imported fbbridge would keep the real paths
ROOT = Path.home() / ".iterm-enhancer"  # the one folder of iterm-enhancer (AC-40)
APP_DIR = Path(os.environ.get("FB_APP_DIR") or ROOT / "state")
LOG_DIR = ROOT / "logs"

SCRIPT = "fb_bridge.py"

AUTOMATION_FIX = ("macOS does not let this terminal control iTerm2. Allow it in System Settings → "
                  "Privacy & Security → Automation (iTerm2 under your terminal app), then run make install again.")
NOT_LISTED_FIX = ("iTerm2 has not listed the new AutoLaunch script yet (it reads the folder when it starts): "
                  "restart iTerm2 and the Files panel starts by itself.")
NOT_LISTED_WAIT = 15.0  # seconds iTerm2 takes to notice a script placed after it started
API_FIX = ("iTerm2 did not run the script. Turn on iTerm2 → Settings → General → Magic → "
           "Enable Python API (and confirm iTerm2's prompt), then run make install again.")


class LaunchError(Exception):
    """iTerm2 refused to run the bridge; the message says how to fix it."""


def iterm_running():
    """iTerm2's app process is up (`ps`: `pgrep -x iTerm2` misses it from some shells)."""
    out = subprocess.run(["ps", "-axo", "comm"], capture_output=True, text=True).stdout
    return any(line.endswith("/iTerm.app/Contents/MacOS/iTerm2") for line in out.splitlines())


def launch():
    """Ask the running iTerm2 to start the AutoLaunch bridge (a new one takes over from the
    old one, AC-30). Never starts iTerm2: call only when iterm_running(). A first install
    places the script after iTerm2 started, so "Script not found" is asked again for a while."""
    deadline = time.monotonic() + NOT_LISTED_WAIT
    while True:
        r = subprocess.run(["osascript", "-e", f'tell application "iTerm2" to launch API script named "{SCRIPT}"'],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return
        err = (r.stderr or r.stdout).strip()
        if "Script not found" not in err:
            break
        if time.monotonic() >= deadline:
            raise LaunchError(f"{NOT_LISTED_FIX} ({err})")
        time.sleep(1)
    if "-1743" in err or "Not authorized" in err:
        raise LaunchError(f"{AUTOMATION_FIX} ({err})")
    raise LaunchError(f"{API_FIX} ({err or f'osascript exited {r.returncode}'})")


def health():
    """fbd's health through its private socket (never the token over TCP: another program
    may hold the port, AC-07), or None while it does not answer."""
    try:
        status, body = unixhttp.request(APP_DIR / "fbd.sock", "GET", "/health", timeout=1)
        return json.loads(body) if status == 200 else None
    except (OSError, ValueError):
        return None


def runs(h, build):
    """Health `h` shows `build` live: its fbd answers and its bridge is connected."""
    return bool(h) and h.get("build") == build and h.get("bridge_connected") and h.get("bridge_build") == build


def wait_healthy(build, seconds=30.0):
    """True once fbd of `build` answers with a connected bridge of the same build. A takeover
    (up to 5.5 s), fbd's start (up to 5 s) and the tool registration fit well within 30 s."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if runs(health(), build):
            return True
        time.sleep(0.25)
    return False


def describe_health():
    """What is running now, for failure messages."""
    h = health()
    if not h:
        return "fbd does not answer"
    return f"fbd build {h.get('build', 'unknown')}, bridge build {h.get('bridge_build') or 'none'}"
