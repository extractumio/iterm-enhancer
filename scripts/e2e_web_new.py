#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52 end-to-end: the web app's [+] in real iTerm2 opens the new session right after the
selected pane, in its folder.

A plain window of its own with two tabs in two folders: [+] with the first tab's pane shown
opens a tab between them, in the first one's folder. A private tmux server (-f /dev/null)
attached with -CC, its first pane in a folder named with quotes, `#(…)` and `$`: [+] opens a
tmux window right after it in that folder, and no command in the name runs. (A folder name with
a line break is left to the unit tests: iTerm2 drops the -CC connection when a tmux window opens
in one.) Types nothing.
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import iterm2

from e2e_common import check, own_window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from fbbridge.resolve import resolve  # noqa: E402
from fbbridge.web.newsession import new_session  # noqa: E402

TMUX = shutil.which("tmux") or "/opt/homebrew/bin/tmux"


def shell_in(folder):
    p = iterm2.LocalWriteOnlyProfile()
    for key, value in {"Custom Command": "Yes", "Command": "/bin/bash --noprofile --norc", "Initial Text": "",
                       "Load Shell Integration Automatically": False, "Custom Directory": "Yes",
                       "Working Directory": folder}.items():
        p._simple_set(key, value)
    return p


def gateway(sock, folder):
    p = iterm2.LocalWriteOnlyProfile()
    p._simple_set("Custom Command", "Yes")
    p._simple_set("Command", f"{TMUX} -S {sock} -f /dev/null -CC new-session -s plus")
    p._simple_set("Initial Text", "")
    p._simple_set("Custom Directory", "Yes")
    p._simple_set("Working Directory", folder)
    return p


def tmux(sock, *args):
    return subprocess.run([TMUX, "-S", sock, *args], capture_output=True, text=True).stdout


async def folder_of(conn, app, sid):
    """The new shell's folder, once it has started."""
    for _ in range(50):
        await asyncio.sleep(0.1)
        s = app.get_session_by_id(sid)
        cwd = (await resolve(conn, s)).get("cwd") if s else None
        if cwd:
            return os.path.realpath(cwd)
    return None


async def main(conn):
    app = await iterm2.async_get_app(conn)
    before = {w.window_id for w in app.terminal_windows}
    root = os.path.realpath(tempfile.mkdtemp(prefix="fbp-", dir="/tmp"))
    sock = f"{root}/s"                                 # Unix socket paths are short
    one, two = f"{root}/one", f"{root}/two"
    ran = f"{Path(root).name}-ran"                    # what #(…) in the folder name would make
    odd = f"{root}/it's #(touch {ran}) $HOME"
    for d in (one, two, odd):
        os.makedirs(d)

    def own():
        return [w for w in app.terminal_windows if w.window_id not in before]

    ok, tc = True, None
    try:
        window = await iterm2.Window.async_create(conn, profile_customizations=shell_in(one))
        own_window(window)
        await window.async_create_tab(profile_customizations=shell_in(two))
        await asyncio.sleep(1)
        await app.async_refresh()
        window = app.get_window_by_id(window.window_id)
        first, second = window.tabs
        sid, note = await new_session(conn, app, window.window_id, first.current_session.session_id)
        await asyncio.sleep(0.5)
        await app.async_refresh()
        order = [t.tab_id for t in app.get_window_by_id(window.window_id).tabs]
        made = app.get_session_by_id(sid)
        ok &= check("AC-52 [+]: the tab opens right after the selected one",
                    made and order == [first.tab_id, made.tab.tab_id, second.tab_id], str(order))
        cwd = await folder_of(conn, app, sid)
        ok &= check("AC-52 [+]: in the selected pane's folder, without a note", cwd == one and note is None, f"{cwd} {note}")

        await iterm2.Window.async_create(conn, profile_customizations=gateway(sock, odd))
        for _ in range(50):
            await asyncio.sleep(0.2)
            for c in await iterm2.async_get_tmux_connections(conn):
                if "plus" in await c.async_send_command("display -p '#{session_name}'"):
                    tc = c
            if tc:
                break
        if not tc:
            raise RuntimeError("The private tmux -CC session did not attach")
        tmux(sock, "new-window", "-c", two)            # a second tmux window, after which nothing goes
        await asyncio.sleep(1.5)
        await app.async_refresh()
        holder = next(w for w in own() if any(t.tmux_connection_id == tc.connection_id for t in w.tabs))
        top = next(t for t in holder.tabs if str(t.tmux_window_id).lstrip("@") == "0")
        sid, note = await new_session(conn, app, holder.window_id, top.current_session.session_id)
        await asyncio.sleep(0.8)
        await app.async_refresh()
        made = app.get_session_by_id(sid)
        tabs = app.get_window_by_id(holder.window_id).tabs
        windows = tmux(sock, "list-windows", "-F", "#{window_id} #{pane_current_path}").splitlines()
        ok &= check("AC-52 [+]: a tmux window opens right after the selected one, in tmux too",
                    made and tabs.index(made.tab) == tabs.index(top) + 1 and windows[1].startswith(f"@{made.tab.tmux_window_id.lstrip('@')} "),
                    f"{[t.tmux_window_id for t in tabs]} {windows}")
        ok &= check("AC-52 [+]: in the tmux pane's folder, named with quotes, #(…) and $, which ran nothing",
                    windows[1].endswith(f" {odd}") and note is None
                    and not any(os.path.exists(f"{d}/{ran}") for d in (root, odd, Path.home(), "/")), f"{windows[1]} {note}")
    finally:
        if tc:
            try:
                await tc.async_send_command("detach")
            except Exception as e:  # already gone: the server is killed below anyway
                print(f"detach: {e}")
        await asyncio.sleep(0.8)
        tmux(sock, "kill-server")
        await asyncio.sleep(0.5)
        await app.async_refresh()
        for w in own():
            await w.async_close(force=True)
        shutil.rmtree(root, ignore_errors=True)
    print("PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


iterm2.run_until_complete(main)
