# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Shared by the iTerm2 end-to-end scripts: the installed fbd's address and token (the
bridge's own settings), a JSON call that returns errors instead of raising, and checks."""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
from fbbridge.common import APP_DIR, PORT, ROOT  # noqa: E402,F401

if not os.environ.get("FB_APP_DIR") or APP_DIR.resolve() == (ROOT / "state").resolve():
    raise RuntimeError("Integration tests require an isolated FB_APP_DIR; the live installation is refused")
TOKEN = (APP_DIR / "token").read_text().strip()
REPO = Path(__file__).resolve().parent.parent


def own_window(window):
    """Tell the private follower exactly which window this test created."""
    if os.environ.get("FB_E2E_ISOLATED") == "1":
        (APP_DIR / "test-windows.json").write_text(json.dumps([window.window_id]))


async def new_window(conn):
    import iterm2
    if os.environ.get("FB_E2E_ISOLATED") == "1":
        profile = iterm2.LocalWriteOnlyProfile()
        profile._simple_set("Custom Command", "Yes")
        profile._simple_set("Command", "/bin/bash --noprofile --norc")
        profile._simple_set("Initial Text", "")
        profile._simple_set("Load Shell Integration Automatically", False)
        window = await iterm2.Window.async_create(conn, profile_customizations=profile)
    else:
        window = await iterm2.Window.async_create(conn)
    if not window:
        raise RuntimeError("The test window did not open")
    own_window(window)
    return window


def call(method, path, body=None):
    """(status, JSON body) of a request to fbd with the panel's token."""
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-FB-Token": TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, passed, detail=""):
    print(f"{'PASS' if passed else 'FAIL'} {name}{f' ({detail})' if detail else ''}")
    return bool(passed)
