#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-25 / AC-26 end-to-end in iTerm2: a new window gets the Toolbelt, ⌘-click opens one
viewer window (a browser, even when iTerm2 holds the profile as a terminal profile, as after
a restart) and reuses it, and the viewer never becomes "the focused pane".
Needs the installed bridge (make install && make restart). Closes what it opens."""
import asyncio

import iterm2

from e2e_common import REPO, call
from fbbridge.viewer_profile import VIEWER_GUID, VIEWER_PROFILE


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
        # what iTerm2 does at launch: the dynamic profile is held as a terminal profile
        for pp in await iterm2.PartialProfile.async_query(conn, guids=[VIEWER_GUID]):
            await (await pp.async_get_full_profile())._async_simple_set("Custom Command", "No")
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
            session = viewer.current_tab.current_session
            prof = await session.async_get_variable("profileName")
            print(f"{'PASS' if prof == VIEWER_PROFILE else 'FAIL'} AC-26 viewer uses the '{VIEWER_PROFILE}' profile ({prof})")
            ok &= prof == VIEWER_PROFILE
            await asyncio.sleep(0.5)
            tty = await session.async_get_variable("tty")
            kind = (await session.async_get_profile()).all_properties.get("Custom Command")
            passed = tty is None and kind == "Browser"
            ok &= passed
            print(f"{'PASS' if passed else 'FAIL'} AC-26 viewer is a browser after the profile was held as a terminal (tty={tty}, {kind})")
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
