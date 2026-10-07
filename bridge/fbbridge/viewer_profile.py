# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The "Files Viewer" browser profile that viewer windows (AC-26) open with. Kept free of
the iterm2 module so its rules run in unit tests."""
import asyncio
import json
from pathlib import Path

from .common import UserError, log

VIEWER_PROFILE = "Files Viewer"
VIEWER_GUID = "9F7C4C2B-5E21-4B0F-A6C2-F11E5B0A0C01"
DYNAMIC_PROFILE = Path.home() / "Library/Application Support/iTerm2/DynamicProfiles/iterm-enhancer.json"
BROWSER = "Browser"
NOT_BROWSER = ("Viewer window failed: the 'Files Viewer' profile did not load as a browser "
               "(is the iTerm2 browser plugin installed?)")


def install_viewer_profile(force=False, path=DYNAMIC_PROFILE):
    """A browser profile for viewer windows (users need not have one). iTerm2 watches the
    DynamicProfiles folder and reloads every file on any write, so `force` re-reads it."""
    profile = {"Profiles": [{"Name": VIEWER_PROFILE, "Guid": VIEWER_GUID, "Custom Command": BROWSER,
                             "Show Status Bar": False, "Tags": ["iterm-enhancer"]}]}
    text = json.dumps(profile, indent=2)
    try:
        if force or not path.exists() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    except OSError as e:
        log(f"viewer profile not written: {e}")


async def ensure_browser(kind, rewrite, timeout=3.0, step=0.25):
    """Make sure iTerm2 holds the viewer profile as a browser profile. At launch iTerm2 3.7
    loads dynamic profiles before its browser plugin and keeps this one as a terminal
    profile; a rewrite makes it load the file again. `kind()` returns the stored
    "Custom Command" (None: no such profile)."""
    if await kind() == BROWSER:
        return
    rewrite()
    for _ in range(int(timeout / step)):
        await asyncio.sleep(step)
        if await kind() == BROWSER:
            log("viewer profile reloaded as a browser profile")
            return
    raise UserError(NOT_BROWSER)
