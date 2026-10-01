#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iTerm2 File Browser — bridge between iTerm2 and fbd (iTerm2 AutoLaunch script).

iTerm2 runs every file in AutoLaunch as a script, so this entry stays alone there and
loads the `fbbridge` package from next to it (a checkout), else from the installed build
that `current` names, resolved once: this bridge keeps importing from that build even
after an upgrade switches `current` (AC-33). Installs before versioned builds kept the
package in $FB_APP_DIR/bridge.

Contract: this file only picks the package folder and runs it. A rollback starts the build
before with the newest copy of this file, so it must keep working with any build.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_DIR = Path(os.environ.get("FB_APP_DIR") or Path.home() / "Library/Application Support/iterm-filebrowser")
LIB_DIR = Path(os.environ.get("FB_LIB_DIR") or Path.home() / ".local/lib/iterm-filebrowser")
for candidate in (HERE, (LIB_DIR / "current").resolve() / "bridge", APP_DIR / "bridge"):
    if (candidate / "fbbridge").is_dir():
        sys.path.insert(0, str(candidate))
        break

from fbbridge.app import run  # noqa: E402

if __name__ == "__main__":
    run()
