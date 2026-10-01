#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-25 / AC-26 end-to-end in iTerm2: a new window gets the Toolbelt, ⌘-click opens one
viewer window and reuses it, and the viewer never becomes "the focused pane".
Needs the installed bridge (make install && make restart). Closes what it opens."""
import asyncio
import json
import os
import urllib.request
from pathlib import Path

import iterm2

PORT = int(os.environ.get("FB_PORT", "47821"))
APP_DIR = Path(os.environ.get("FB_APP_DIR") or Path.home() / "Library/Application Support/iterm-filebrowser")
TOKEN = (APP_DIR / "token").read_text().strip()
REPO = Path(__file__).resolve().parent.parent


def call(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-FB-Token": TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.status, json.loads(r.read() or b"null")


async def main(conn):
    app = await iterm2.async_get_app(conn)
    ok, opened = True, []
    for _ in range(40):  # windows that exist before the bridge is up are left alone by design
        if call("GET", "/api/health")[1].get("bridge_connected"):
            break
        await asyncio.sleep(0.25)
    await asyncio.sleep(1.0)
    try:
        before = {w.window_id for w in app.terminal_windows}
        await iterm2.Window.async_create(conn)
        await asyncio.sleep(0.5)
        term = next(w for w in app.terminal_windows if w.window_id not in before)
        opened.append(term)
        await app.async_activate(raise_all_windows=False, ignoring_other_apps=True)
        await term.async_activate()
        shown = False
        for _ in range(12):
            await asyncio.sleep(0.25)
            shown = (await iterm2.MainMenu.async_get_menu_item_state(conn, "Show Toolbelt")).checked
            if shown:
                break
        ok &= shown
        print(f"{'PASS' if shown else 'FAIL'} AC-25 new window shows the Toolbelt")

        await term.current_tab.current_session.async_send_text(f"cd {REPO}\r")
        await asyncio.sleep(1.2)
        terminal_key = call("GET", "/api/state")[1].get("key")
        count = len(app.terminal_windows)
        call("POST", "/api/view/open", {"path": str(REPO / "README.md")})
        viewer = None
        for _ in range(20):
            await asyncio.sleep(0.25)
            viewer = next((w for w in app.terminal_windows if w.window_id not in before and w is not term), None)
            if viewer and viewer.current_tab:
                break
        passed = viewer is not None and len(app.terminal_windows) == count + 1
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-26 ⌘-click opens a viewer window")
        if viewer:
            opened.append(viewer)
            prof = await viewer.current_tab.current_session.async_get_variable("profileName")
            print(f"{'PASS' if prof == 'Files Viewer' else 'FAIL'} AC-26 viewer uses the 'Files Viewer' profile ({prof})")
            ok &= prof == "Files Viewer"
            await asyncio.sleep(1.5)  # focused: the bridge must keep following the terminal
            key_now = call("GET", "/api/state")[1].get("key")
            passed = key_now == terminal_key
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'} AC-26 viewer focus does not replace the terminal pane")
            call("POST", "/api/view/open", {"path": str(REPO / "Makefile")})
            await asyncio.sleep(1.5)
            passed = len(app.terminal_windows) == count + 1
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'} AC-26 a second file reuses the viewer window")
            frame = await viewer.async_get_frame()
            print(f"     viewer frame {int(frame.size.width)}×{int(frame.size.height)}")
    finally:
        for w in opened:
            await w.async_close(force=True)
    print("PASS" if ok else "FAIL")


iterm2.run_until_complete(main)
