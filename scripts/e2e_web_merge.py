#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52 end-to-end: the web app's "Merge windows" in real iTerm2.

Opens two plain windows of its own and a private tmux server (-f /dev/null) attached with
-CC, whose two tmux windows are put in two iTerm2 windows; merges only these four windows
through the bridge's merge_windows() and checks that two remain (one plain, one tmux), that
tmux kept both its windows, and that no other window changed. Types nothing into a session.
"""
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import iterm2

from e2e_common import check, new_window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from fbbridge.web.merge import merge_windows  # noqa: E402

TMUX = shutil.which("tmux") or "/opt/homebrew/bin/tmux"


def gateway(sock):
    p = iterm2.LocalWriteOnlyProfile()
    p._simple_set("Custom Command", "Yes")
    p._simple_set("Command", f"{TMUX} -S {sock} -f /dev/null -CC new-session -s merge")
    p._simple_set("Initial Text", "")
    return p


def tmux_windows(sock):
    out = subprocess.run([TMUX, "-S", sock, "list-windows", "-F", "#{window_id}"], capture_output=True, text=True)
    return sorted(out.stdout.split())


async def main(conn):
    app = await iterm2.async_get_app(conn)
    before = {w.window_id for w in app.terminal_windows}
    others = {w.window_id: [t.tab_id for t in w.tabs] for w in app.terminal_windows}
    folder = tempfile.mkdtemp(prefix="fbm-", dir="/tmp")
    sock = f"{folder}/s"                              # Unix socket paths are short

    def own():
        return [w for w in app.terminal_windows if w.window_id not in before]

    ok, tc = True, None
    try:
        await new_window(conn)
        await new_window(conn)
        await iterm2.Window.async_create(conn, profile_customizations=gateway(sock))
        for _ in range(50):
            await asyncio.sleep(0.2)
            for c in await iterm2.async_get_tmux_connections(conn):
                if "merge" in await c.async_send_command("display -p '#{session_name}'"):
                    tc = c
            if tc:
                break
        if not tc:
            raise RuntimeError("The private tmux -CC session did not attach")
        await tc.async_send_command("new-window")
        await asyncio.sleep(1.5)
        await app.async_refresh()
        holders = [w for w in own() if any(t.tmux_connection_id == tc.connection_id for t in w.tabs)]
        if len(holders) == 1 and len(holders[0].tabs) == 2:
            await holders[0].tabs[1].async_move_to_window()     # iTerm2 may open the second as a tab
            await asyncio.sleep(0.8)
            await app.async_refresh()
        shape = sorted(len(w.tabs) for w in own())
        ok &= check("AC-52 merge: four windows of its own to start", shape == [1, 1, 1, 1], str(shape))
        kept = tmux_windows(sock)

        text = await merge_windows(app, own())
        await asyncio.sleep(0.8)
        await app.async_refresh()
        kinds = sorted(sorted(t.tmux_window_id not in (None, "-1") for t in w.tabs) for w in own())
        ok &= check("AC-52 merge: one plain window and one tmux window remain", kinds == [[False, False], [True, True]], f"{text} {kinds}")
        ok &= check("AC-52 merge: tmux kept its windows", tmux_windows(sock) == kept and len(kept) == 2, str(kept))
        now = {w.window_id: [t.tab_id for t in w.tabs] for w in app.terminal_windows if w.window_id in before}
        ok &= check("AC-52 merge: no other window changed", now == others)
    finally:
        if tc:
            try:
                await tc.async_send_command("detach")
            except Exception as e:  # already gone: the server is killed below anyway
                print(f"detach: {e}")
        await asyncio.sleep(0.8)
        subprocess.run([TMUX, "-S", sock, "kill-server"], capture_output=True)
        await asyncio.sleep(0.5)
        await app.async_refresh()
        for w in own():
            await w.async_close(force=True)
        shutil.rmtree(folder, ignore_errors=True)
    print("PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


iterm2.run_until_complete(main)
