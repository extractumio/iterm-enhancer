# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iTerm2 keeps every registered web-view tool in its preferences forever
(`NoSyncDynamicTools`) and has no API to unregister one. These helpers keep that list
healthy: at bridge start, any entry that points at our backend gets the current URL, and
`clean` (iTerm2 must not be running) removes our entries on uninstall."""
import plistlib
import subprocess

import iterm2

from .common import PORT, TOOL_ID, log

DOMAIN = "com.googlecode.iterm2"
KEY = "NoSyncDynamicTools"


def registrations():
    """{identifier: {"name", "URL"}} as iTerm2 stored them, or {} if unreadable."""
    out = subprocess.run(["defaults", "export", DOMAIN, "-"], capture_output=True).stdout
    try:
        return plistlib.loads(out).get(KEY, {}) if out else {}
    except Exception:
        return {}


def ours(url):
    return f"://127.0.0.1:{PORT}/" in (url or "")


async def heal(conn, url):
    """Point every other registration of our backend (an old token, a former identifier)
    at the current URL, so no Toolbelt can load a panel that fbd will refuse."""
    for ident, entry in registrations().items():
        if ident != TOOL_ID and ours(entry.get("URL")) and entry.get("URL") != url:
            await iterm2.tool.async_register_web_view_tool(conn, entry.get("name", "Files"), ident, False, url)
            log(f"healed stale tool registration {ident}")
