# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iTerm2 keeps every registered web-view tool in its preferences forever
(`NoSyncDynamicTools`) and has no API to unregister one. These helpers keep that list
healthy: at bridge start, any entry that points at our backend gets the current URL, and
`scripts/clean_registrations.py` (iTerm2 must not be running) removes our entries on
uninstall. Only `heal` needs the iterm2 module, so the script can import the rest."""
import hashlib
import json
import plistlib
import subprocess

from .common import APP_DIR, PORT, TOOL_ID, log

DOMAIN = "com.googlecode.iterm2"
KEY = "NoSyncDynamicTools"


def registrations(domain=DOMAIN):
    """{identifier: {"name", "URL"}} as iTerm2 stored them, or {} if unreadable."""
    out = subprocess.run(["defaults", "export", domain, "-"], capture_output=True).stdout
    try:
        return plistlib.loads(out).get(KEY, {}) if out else {}
    except Exception:
        return {}


MARKER = APP_DIR / "registered.json"


def _digest(url, epoch):
    return hashlib.sha256(f"{epoch}\n{url}".encode()).hexdigest()  # the URL carries the token


def registered_here(url, epoch, marker=None):
    """True if this iTerm2 process already has the tool registered with `url` (a bridge
    restart inside it, AC-36): registering again would reload every panel as a new web
    view and drop its window binding. Without an epoch nothing is known."""
    try:
        return bool(epoch) and json.loads((marker or MARKER).read_text()).get("id") == _digest(url, epoch)
    except (OSError, ValueError, AttributeError):
        return False


def remember(url, epoch, marker=None):
    if not epoch:
        return
    try:
        (marker or MARKER).write_text(json.dumps({"id": _digest(url, epoch)}))
    except OSError as e:
        log(f"registration not remembered: {e}")


def ours(url, port=PORT):
    return f"://127.0.0.1:{port}/" in (url or "")


async def heal(conn, url):
    """Point every other registration of our backend (an old token, a former identifier)
    at the current URL, so no Toolbelt can load a panel that fbd will refuse."""
    import iterm2
    for ident, entry in registrations().items():
        if ident != TOOL_ID and ours(entry.get("URL")) and entry.get("URL") != url:
            await iterm2.tool.async_register_web_view_tool(conn, entry.get("name", "Files"), ident, False, url)
            log(f"healed stale tool registration {ident}")
