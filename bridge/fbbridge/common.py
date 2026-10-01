# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Settings and logging shared by the bridge modules."""
import os
import socket
import time
from pathlib import Path

PORT = int(os.environ.get("FB_PORT", "47821"))
BASE = f"http://127.0.0.1:{PORT}"
APP_DIR = Path(os.environ.get("FB_APP_DIR") or Path.home() / "Library/Application Support/iterm-filebrowser")
LOG_DIR = Path.home() / "Library/Logs/iterm-filebrowser"
TOOL_ID = "com.local.iterm-filebrowser"
POLL = 0.5
POLL_TIMEOUT = 10.0  # one poll of the focused pane (AC-30)
HEARTBEAT = 5.0
THEME_EVERY = 4  # polls (≈2 s) between profile re-reads
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
