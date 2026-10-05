#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-12 end-to-end: the panel's "Insert Path" and "Open Terminal Here" reach the pane.

Opens its own iTerm2 window and sends commands only after fbd reports that this window's
session has focus, so nothing is ever typed into another terminal. Needs the installed
bridge (make install && make restart).
"""
import asyncio
import tempfile
from pathlib import Path

import iterm2

from e2e_common import call, new_window

TARGET = Path(tempfile.mkdtemp(prefix="fb term ")).resolve()  # a space, to test quoting


async def main(conn):
    app = await iterm2.async_get_app(conn)
    created = await new_window(conn)
    await asyncio.sleep(1.5)
    win = app.get_window_by_id(created.window_id)
    s = win.current_tab.current_session
    ok = True
    try:
        await win.async_activate()
        for _ in range(40):
            await asyncio.sleep(0.25)
            if call("GET", "/api/state")[1].get("session") == s.session_id:
                break
        else:
            print("FAIL focus: fbd never reported the test window; nothing sent")
            raise SystemExit(1)
        key = call("GET", "/api/state")[1]["key"]
        # a name with control characters must never reach the terminal
        status, body = call("POST", "/api/terminal/insert", {"paths": ["a\x03echo PWNED\r"], "key": key})
        passed = status == 400
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-12 control characters refused → {status} {(body or {}).get('error')}")
        # the panel shows another pane than the focused one → refused
        status, body = call("POST", "/api/terminal/insert", {"paths": ["x"], "key": "some-other-pane"})
        passed = status == 409 and (body or {}).get("error") == "focus_changed"
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-12 stale pane key refused → {status} {(body or {}).get('error')}")
        # Insert Path: typed without Enter
        status, _ = call("POST", "/api/terminal/insert", {"paths": ["src/db/pool.rs"], "key": key})
        await asyncio.sleep(0.7)
        screen = await s.async_get_screen_contents()
        lines = [screen.line(i).string for i in range(screen.number_of_lines)]
        line = next((x for x in reversed(lines) if x.strip()), "")
        if any("PWNED" in x for x in lines):
            print("FAIL control characters reached the terminal"); ok = False
        passed = status == 204 and line.rstrip().endswith("src/db/pool.rs")
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-12 insert path → prompt line ends with {line.strip()[-20:]!r}")
        await s.async_send_text("\x15", suppress_broadcast=True)  # clear the prompt line
        # Open Terminal Here: cd typed with quoting after what is already on the line, never
        # run by fbd (the user presses Return); then the pane follows
        await s.async_send_text("touch PWNED-BY-CD; ", suppress_broadcast=True)
        status, _ = call("POST", "/api/terminal/cd", {"path": str(TARGET), "key": key})
        await asyncio.sleep(1.0)
        screen = await s.async_get_screen_contents()
        # the typed line is longer than the window: join soft-wrapped rows into lines
        rows, lines = [screen.line(i) for i in range(screen.number_of_lines)], [""]
        for r in rows:
            lines[-1] += r.string
            if r.hard_eol:
                lines.append("")
        line = next((x for x in reversed(lines) if x.strip()), "")
        passed = status == 204 and line.rstrip().endswith(f"cd '{TARGET}'") and call("GET", "/api/state")[1].get("cwd") != str(TARGET)
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-12 open terminal here types, runs nothing → {line.strip()[-40:]!r}")
        await s.async_send_text("\x15", suppress_broadcast=True)  # clear the line, then type the cd alone and run it
        call("POST", "/api/terminal/cd", {"path": str(TARGET), "key": key})
        await asyncio.sleep(0.5)
        await s.async_send_text("\r", suppress_broadcast=True)
        for _ in range(20):
            await asyncio.sleep(0.25)
            if call("GET", "/api/state")[1].get("cwd") == str(TARGET):
                break
        cwd = call("GET", "/api/state")[1].get("cwd")
        passed = status == 204 and cwd == str(TARGET)
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-12 open terminal here → cwd {cwd!r}")
        # busy terminal: cd refused, nothing typed
        await s.async_send_text("sleep 5\r", suppress_broadcast=True)
        await asyncio.sleep(1.2)
        st = call("GET", "/api/state")[1]
        status, body = call("POST", "/api/terminal/cd", {"path": "/tmp", "key": key})
        passed = status == 409 and "busy" in (body or {}).get("error", "")
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-12 busy terminal → {status} {(body or {}).get('message')}"
              + ("" if passed else f" (state job={st.get('job')} busy={st.get('busy')} note={st.get('note')})"))
        await s.async_send_text("\x03", suppress_broadcast=True)
    finally:
        await asyncio.sleep(0.3)
        await win.async_close(force=True)
        TARGET.rmdir()
    print("PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


iterm2.run_until_complete(main)
