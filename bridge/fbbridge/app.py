# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The bridge's main loop: take the bridge lock, start fbd, register the tool, follow the
focused pane, and run the panel's commands (type into the terminal, open a viewer window,
Toolbelt width). It exits with its iTerm2 and takes fbd with it (AC-30)."""
import asyncio
import atexit
import os
import signal
import time
import urllib.error

import iterm2

from .backend import Backend
from .common import APP_DIR, BASE, HEARTBEAT, POLL, POLL_TIMEOUT, THEME_EVERY, TOOL_ID, UserError, log
from .lifecycle import LockError, bounded, connection_closed, take_lock, watch
from .procinfo import iterm_process
from .registry import heal
from .resolve import resolve, static_vars, theme_of
from .viewer_profile import install_viewer_profile
from .windows import Windows

backend = Backend()


def stop(reason, code=0):
    """Take fbd down with us so the port is free at once, then exit without waiting for
    asyncio (it may be stuck on a call to a dead connection)."""
    log(f"exit: {reason}")
    backend.stop()
    os._exit(code)


atexit.register(backend.stop)
signal.signal(signal.SIGTERM, lambda *_: stop("SIGTERM"))  # Script Console, a newer bridge


async def run_commands(conn, app, windows, queue):
    while True:
        c = await queue.get()
        try:
            action = c.get("action")
            if action == "type":
                s = app.get_session_by_id(c.get("session", ""))
                if s is None:
                    raise UserError("The terminal pane is gone — command not sent")
                await s.async_send_text(c["text"])
                await s.async_activate()
            elif action == "viewer":
                await windows.open_viewer(conn, c["path"], c["code"])
            elif action == "default-width":
                await windows.set_default_width(conn)
        except Exception as e:
            await asyncio.get_running_loop().run_in_executor(None, backend.report_failure, c, e)


async def is_terminal(sess, windows):
    """Browser sessions (the viewer window, web profiles) have no process and no tmux
    pane: the panel keeps following the last terminal instead."""
    if sess is None or windows.is_viewer(sess):
        return False
    return any(await static_vars(sess)) or await sess.async_get_variable("tmuxRole") == "client"


class Follower:
    """Pushes the focused pane's state to fbd: on change, else every HEARTBEAT."""

    def __init__(self, conn, app, windows):
        self.conn, self.app, self.windows = conn, app, windows
        self.last, self.last_sent, self.tick, self.theme, self.theme_session = None, 0.0, 0, None, None

    async def push(self, state):
        await asyncio.get_running_loop().run_in_executor(None, backend.post, "/internal/state", state)
        self.last, self.last_sent = state, time.monotonic()

    async def poll(self):
        self.tick += 1
        app, windows = self.app, self.windows
        await windows.tick(self.conn)
        win = app.current_terminal_window
        sess = win.current_tab.current_session if win and win.current_tab else None
        if await is_terminal(sess, windows):
            if sess.session_id != self.theme_session or self.tick % THEME_EVERY == 0:
                self.theme, self.theme_session = await theme_of(app, sess), sess.session_id
            r = await resolve(self.conn, sess)
            cwd = os.path.realpath(r["cwd"]) if r.get("cwd") else None
            state = {"window": win.window_id, "session": sess.session_id, "key": r["key"],
                     "title": await sess.async_get_variable("presentationName") or "",
                     "mode": r["mode"], "note": r.get("note", ""), "job": r.get("job"),
                     "busy": r.get("busy", False), "theme": self.theme}
            last = self.last
            if cwd:
                state.update(cwd=cwd, stale=False)
            elif last and last.get("key") == r["key"]:
                state.update(cwd=last.get("cwd"), stale=True)  # keep the last tree, mark stale
            else:
                state.update(cwd=None, stale=True)
            if state != last or time.monotonic() - self.last_sent > HEARTBEAT:
                await self.push(state)
                backend.failures = 0
        elif self.last and time.monotonic() - self.last_sent > HEARTBEAT:
            await self.push(self.last)  # stay "connected"


async def main(conn):
    loop = asyncio.get_running_loop()
    # main runs once the API connection is up, so a bridge that cannot connect evicts nobody;
    # the lock is held until this process exits
    try:
        await loop.run_in_executor(None, take_lock, APP_DIR / "bridge.lock")
    except LockError as e:
        log(str(e))
        raise SystemExit(1)
    iterm = iterm_process()
    watch(stop, lambda: connection_closed(conn.websocket), iterm if iterm and iterm[1] else None)
    backend.start()
    if not await loop.run_in_executor(None, backend.wait_ready):
        stop("fbd did not start; see fbd.log", 1)
    token = (APP_DIR / "token").read_text().strip()
    url = f"{BASE}/?t={token}"
    await iterm2.tool.async_register_web_view_tool(conn, "Files", TOOL_ID, False, url)
    await heal(conn, url)
    install_viewer_profile()
    log("tool registered")

    app = await iterm2.async_get_app(conn)
    windows = Windows(app, backend)
    await windows.adopt_viewer()
    commands: asyncio.Queue = asyncio.Queue()
    backend.listen_commands(loop, commands)
    asyncio.create_task(run_commands(conn, app, windows, commands))

    follower = Follower(conn, app, windows)
    while True:
        try:
            if not backend.alive():
                backend.failures += 1
                if backend.failures > 5:
                    stop("fbd keeps exiting; giving up", 1)
                await asyncio.sleep(min(2 * backend.failures, 10))
                backend.start()
                await loop.run_in_executor(None, backend.wait_ready)
                follower.last = None  # re-push state to the fresh process
            if not await bounded(follower.poll(), POLL_TIMEOUT):  # costs one poll, not the loop
                log(f"poll timed out after {POLL_TIMEOUT:g} s")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            log(f"push failed: {e}")
        except Exception as e:  # keep tracking whatever happens
            log(f"error: {type(e).__name__}: {e}")
        await asyncio.sleep(POLL)


def run():
    iterm2.run_forever(main, retry=True)
