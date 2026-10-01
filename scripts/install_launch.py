# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Launching the installed bridge through iTerm2 and waiting until the new build answers
(AC-33). Kept apart from the file work in install.py; tests replace these functions."""
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

# read here, not imported from fbbridge.common: tests reload this module under a temporary
# home, and an already imported fbbridge would keep the real paths
PORT = int(os.environ.get("FB_PORT", "47821"))
APP_DIR = Path(os.environ.get("FB_APP_DIR") or Path.home() / "Library/Application Support/iterm-filebrowser")
LOG_DIR = Path.home() / "Library/Logs/iterm-filebrowser"

SCRIPT = "fb_bridge.py"

AUTOMATION_FIX = ("macOS does not let this terminal control iTerm2. Allow it in System Settings → "
                  "Privacy & Security → Automation (iTerm2 under your terminal app), then run make install again.")
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
    old one, AC-30). Never starts iTerm2: call only when iterm_running()."""
    r = subprocess.run(["osascript", "-e", f'tell application "iTerm2" to launch API script named "{SCRIPT}"'],
                       capture_output=True, text=True, timeout=30)
    if r.returncode == 0:
        return
    err = (r.stderr or r.stdout).strip()
    if "-1743" in err or "Not authorized" in err:
        raise LaunchError(f"{AUTOMATION_FIX} ({err})")
    raise LaunchError(f"{API_FIX} ({err or f'osascript exited {r.returncode}'})")


def health():
    """fbd's /api/health, or None while it does not answer."""
    try:
        token = (APP_DIR / "token").read_text().strip()
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/health", headers={"X-FB-Token": token})
        with urllib.request.urlopen(req, timeout=1) as r:
            return json.loads(r.read())
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
