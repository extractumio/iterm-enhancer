#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52 end-to-end: the web app's pencil names a pane in real iTerm2, as plain text.

Opens its own iTerm2 window, names its session through the bridge's rename() with a name
that iTerm2 would interpolate if it were not escaped, reads the tab title and session name
back, then clears them. Touches no other window and types nothing.
"""
import asyncio
import sys
from pathlib import Path

import iterm2

from e2e_common import new_window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from fbbridge.web.rename import rename  # noqa: E402

NAME = r"build \(session.path) #1"


async def main(conn):
    app = await iterm2.async_get_app(conn)
    created = await new_window(conn)
    await asyncio.sleep(1.0)
    win = app.get_window_by_id(created.window_id)
    s = win.current_tab.current_session
    ok = True
    try:
        await rename(conn, s, NAME)
        await asyncio.sleep(0.5)
        title = await win.current_tab.async_get_variable("titleOverride")
        name = await s.async_get_variable("name")
        shown = await win.current_tab.async_get_variable("title")
        # iTerm2's "name" variable is the composed title ("<profile>: <name> (<job>) — …"): it holds the name, uninterpolated
        passed = title == NAME and NAME in (name or "") and NAME in (shown or "")
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-52 a name is plain text → tab {title!r}, session {name!r}, shown {shown!r}")
        await rename(conn, s, "")
        await asyncio.sleep(0.5)
        title = await win.current_tab.async_get_variable("titleOverride")
        name = await s.async_get_variable("name")
        profile = await s.async_get_variable("profileName")
        passed = not title and NAME not in (name or "") and bool(profile) and profile in (name or "")
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-52 an empty name gives the names back → tab {title!r}, session {name!r}")
    finally:
        await asyncio.sleep(0.3)
        await win.async_close(force=True)
    print("PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


iterm2.run_until_complete(main)
