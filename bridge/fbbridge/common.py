# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Settings and logging shared by the bridge modules."""
import os
import socket
import time
from pathlib import Path

PORT = int(os.environ.get("FB_PORT", "47821"))
BASE = f"http://127.0.0.1:{PORT}"
# everything iterm-enhancer keeps is under one root (AC-40)
ROOT = Path.home() / ".iterm-enhancer"
APP_DIR = Path(os.environ.get("FB_APP_DIR") or ROOT / "state")
SOCKET = APP_DIR / "fbd.sock"  # the bridge talks to fbd only here (AC-07)
# an installed build is <root>/builds/<build>/{fbd, bridge/fbbridge, BUILD} (AC-33); a checkout has no BUILD
BUILD_DIR = Path(__file__).resolve().parents[2]
BUILD = (BUILD_DIR / "BUILD").read_text().strip() if (BUILD_DIR / "BUILD").is_file() else "dev"
LOG_DIR = APP_DIR / "logs" if os.environ.get("FB_APP_DIR") else ROOT / "logs"
TOOL_ID = "com.local.iterm-enhancer"
# where releases are published (the installer's CLI reads the same variable)
RELEASES = os.environ.get("FB_RELEASE_URL", "https://github.com/extractumio/iterm-enhancer/releases")
POLL = 0.5
POLL_TIMEOUT = 10.0  # one poll of the focused pane (AC-30)
HEARTBEAT = 5.0
THEME_EVERY = 4  # polls (≈2 s) between profile re-reads
HOSTS_EVERY = 60  # polls (≈30 s) between checks of every open remote host's helper (AC-42)
AUTO_TOOLBELT = os.environ.get("FB_AUTO_TOOLBELT", "1") != "0"

SHELLS = {"bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "xonsh"}
REMOTE_JOBS = {"ssh", "mosh", "mosh-client", "et", "telnet"}
LOCAL_HOST = socket.gethostname().split(".")[0].lower()


class UserError(Exception):
    """A failure worded for the user: the panel that asked shows it as is."""


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "bridge.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}\n")
