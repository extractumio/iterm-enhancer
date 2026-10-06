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
from . import agentctl, settings
from .common import APP_DIR, BASE, HEARTBEAT, HOSTS_EVERY, POLL, POLL_TIMEOUT, SHELLS, THEME_EVERY, TOOL_ID, UserError, log
from .lifecycle import LockError, bounded, connection_closed, take_lock, watch
from .inventory import Inventory
from .navigation import open_quickly
from .procinfo import iterm_process
from .registry import heal, registered_here, remember
from .resolve import open_hosts, resolve, static_vars, theme_of
from .remote import Remotes
from .updates import Updates
from .recovery_capture import Capture, epoch
from .recovery_lifecycle import Lifecycle
from .recovery_runner import Runner
from .recovery_startup import Startup
from .recovery_quit import QuitWatch
from .viewer_profile import install_viewer_profile
from .windows import Windows

backend = Backend()
remotes = Remotes(backend.post)
_quit = None


def stop(reason, code=0):
    """Take fbd down with us so the port is free at once, then exit without waiting for
    asyncio (it may be stuck on a call to a dead connection)."""
    log(f"exit: {reason}")
    if _quit is not None:
        # API shutdown can precede the app's kernel exit. A bridge takeover only
        # drains already queued evidence, so a live parent never becomes normal Quit.
        _quit.finish(timeout=3 if reason == "iTerm2 API connection closed" else 0)
        _quit.close()
    remotes.stop()
    backend.stop()
    os._exit(code)


atexit.register(backend.stop)
signal.signal(signal.SIGTERM, lambda *_: stop("SIGTERM"))  # Script Console, a newer bridge


def typable(text):
    """No keystrokes or invisible format characters (the same refusal as fbd, AC-12)."""
    return isinstance(text, str) and not any(
        ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f
        or ord(ch) in (0xad, 0x061c, 0x180e, 0xfeff)
        or 0x200b <= ord(ch) <= 0x200f or 0x202a <= ord(ch) <= 0x202e
        or 0x2060 <= ord(ch) <= 0x206f or 0xfff9 <= ord(ch) <= 0xfffb
        for ch in text)


async def send_command(conn, app, command):
    """Resolve immediately before typing: an iTerm2 session can show another tmux pane."""
    session = app.get_session_by_id(command.get("session", ""))
    if session is None:
        raise UserError("The terminal pane is gone — command not sent")
    state = await resolve(conn, session)
    if not command.get("key") or command["key"] != state.get("key"):
        raise UserError("The terminal pane changed — command not sent")
    if "job" not in command or command["job"] != (state.get("job") or ""):
        raise UserError("The terminal job changed — command not sent")
    intent = command.get("intent")
    if intent not in ("insert", "cd"):
        raise UserError("Unknown terminal command — command not sent")
    if intent == "cd":
        idle = state.get("idle", False) if state.get("mode") == "remote" else not state.get("busy", True)
        if not idle or state.get("job") not in SHELLS:
            raise UserError("The terminal is busy — command not sent")
    if not typable(command.get("text")):
        raise UserError("Text with control or invisible characters — not sent to the terminal")
    await session.async_send_text(command["text"], suppress_broadcast=True)
    await session.async_activate()


async def run_commands(conn, app, windows, queue, recovery=None, updates=None):
    while True:
        c = await queue.get()
        try:
            action = c.get("action")
            if action == "type":
                await send_command(conn, app, c)
            elif action == "viewer":
                await windows.open_viewer(conn, c["path"], c["code"], c.get("host"))
            elif action == "default-width":
                await windows.set_default_width(conn)
            elif action == "open-quickly":
                await open_quickly(conn)
            elif action == "save-checkpoint" and recovery:
                async def save(command=c):
                    try:
                        await recovery.capture.save(force=True)
                    except Exception as e:
                        await asyncio.to_thread(backend.report_failure, command, e)
                asyncio.create_task(save())
            elif action == "restore-checkpoint" and recovery:
                recovery.start(c["job"])
            elif action in ("host-enable", "host-dismiss", "host-remove"):  # the panel's buttons (AC-38)
                k = c.get("host", "")
                if action == "host-enable":
                    remotes.enable(k, c.get("by"))
                elif action == "host-dismiss":
                    remotes.dismiss(k)
                else:
                    remotes.remove(k, c.get("by"))
            elif action in ("update-skip", "update-off", "update-on") and updates:  # the chip's menu (AC-51)
                await updates.choose(action[7:], c.get("version"))
            elif action == "which-window":  # its own task: never delays a terminal command
                asyncio.create_task(answer_which_window(conn, app, windows, c))
        except Exception as e:
            await asyncio.get_running_loop().run_in_executor(None, backend.report_failure, c, e)


async def answer_which_window(conn, app, windows, c):
    """A panel was used, so its window is key (AC-36): say which, read after the request."""
    try:
        await asyncio.sleep(POLL)  # let the focus change reach the app model
        win = app.current_terminal_window
        wid = win.window_id if win and win.window_id != windows.viewer_id else None
        # the cached value: re-reading the menu while a new Toolbelt appears can say "hidden"
        shown = bool(wid) and await windows.toolbelt_shown(conn, wid)
        await asyncio.get_running_loop().run_in_executor(
            None, backend.post, "/internal/bound", {"req": c["req"], "window": wid, "panel": shown})
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
        if self.tick % HOSTS_EVERY == 1:  # at start (an upgrade restarts the bridge), then every 30 s
            try:
                for key, target in (await open_hosts(self.conn)).items():
                    remotes.status(key, target, (agentctl.entry(key) or {}).get("name", key))
            except Exception as e:  # never at the cost of following the focused pane
                log(f"open hosts: {type(e).__name__}: {e}")
        win = app.current_terminal_window
        sess = win.current_tab.current_session if win and win.current_tab else None
        if await is_terminal(sess, windows):
            if sess.session_id != self.theme_session or self.tick % THEME_EVERY == 0:
                self.theme, self.theme_session = await theme_of(app, sess), sess.session_id
            r = await resolve(self.conn, sess)
            cwd = os.path.realpath(r["cwd"]) if r.get("cwd") else None  # remote panes: below
            state = {"window": win.window_id, "session": sess.session_id, "key": r["key"],
                     "title": await sess.async_get_variable("presentationName") or "",
                     "mode": r["mode"], "note": r.get("note", ""), "job": r.get("job"),
                     "busy": r.get("busy", False), "theme": self.theme,
                     "panel": await windows.toolbelt_shown(self.conn, win.window_id, refresh=self.tick % THEME_EVERY == 0)}
            last = self.last
            remote_path = False
            if r.get("remote_key"):  # a host's own path, never resolved on this Mac (AC-37, AC-38)
                st = remotes.status(r["remote_key"], r["ssh"], r["remote_host"])
                state["note"] = st["note"]
                state["remote"] = {"key": r["remote_key"], "name": r["remote_host"], "state": st["state"], **({"updated": st["updated"]} if st.get("updated") else {})}
                if st["enabled"]:
                    # always with its host: while disconnected the panel's requests fail
                    # with "not connected" instead of reading the same path on this Mac
                    state.update(host=r["remote_key"], busy=not r["idle"])
                    cwd, remote_path = r.get("path"), True
            if remote_path and not cwd:
                state.update(cwd=None, stale=True)
            elif cwd:
                state.update(cwd=cwd, stale=False)
            elif last and last.get("key") == r["key"]:
                state.update(cwd=last.get("cwd"), stale=True)  # keep the last tree, mark stale
            else:
                state.update(cwd=None, stale=True)
            if state != last or time.monotonic() - self.last_sent > HEARTBEAT:
                await self.push(state)
                backend.failures = 0
        elif (self.last and app.get_session_by_id(self.last.get("session"))
              and app.get_window_by_id(self.last.get("window"))
              and time.monotonic() - self.last_sent > HEARTBEAT):
            await self.push(self.last)  # stay "connected"


_registered = {"url": None}


async def register(conn, epoch=None):
    """Register the Files tool with fbd's current token, if that changed (AC-07); not again
    in the same iTerm2 process with the same URL (AC-36)."""
    url = f"{BASE}/?t={(APP_DIR / 'token').read_text().strip()}"
    if url == _registered["url"]:
        return
    if registered_here(url, epoch):
        log("tool already registered in this iTerm2 process")
    else:
        await iterm2.tool.async_register_web_view_tool(conn, "Files", TOOL_ID, False, url)
        remember(url, epoch)
    await heal(conn, url)
    _registered["url"] = url
    log("tool registered")


async def main(conn):
    global _quit
    loop = asyncio.get_running_loop()
    # main runs once the API connection is up, so a bridge that cannot connect evicts nobody;
    # the lock is held until this process exits
    # a handover (an upgrade, a relaunch: an fbd or a bridge of this install runs) keeps the
    # token, so open panels keep unsaved edits; a cold start makes a new one, because panels
    # restored at iTerm2's launch may have sent the old one to whatever held the port (AC-07)
    ours = await loop.run_in_executor(None, backend.answers)
    try:
        _, took_over = await loop.run_in_executor(None, take_lock, APP_DIR / "bridge.lock")
    except LockError as e:
        log(str(e))
        raise SystemExit(1)
    iterm = iterm_process()
    run_epoch = None
    try:
        run_epoch = await loop.run_in_executor(None, epoch, iterm)
        _, pid, started = run_epoch.rsplit(":", 2)
        iterm = int(pid), int(started)
        _quit = QuitWatch(iterm, lambda: backend.query("POST", "/internal/recovery/exit",
                                                     {"epoch": run_epoch}, timeout=1))
    except Exception as e:
        log(f"Normal application exit tracking unavailable ({type(e).__name__}); recovery history preserved")
    watch(stop, lambda: connection_closed(conn.websocket), iterm if iterm and iterm[1] else None)
    backend.start(new_token=not (ours or took_over))
    if not await loop.run_in_executor(None, backend.wait_ready):
        stop("fbd did not start; see fbd.log", 1)
    await register(conn, run_epoch)
    install_viewer_profile()

    app = await iterm2.async_get_app(conn)
    windows = Windows(app, backend)
    await windows.adopt_viewer()
    commands: asyncio.Queue = asyncio.Queue()
    backend.listen_commands(loop, commands)
    capture = Capture(conn, app, windows, backend)
    capture.epoch = run_epoch
    capture.lifecycle = Lifecycle(capture)
    asyncio.create_task(capture.lifecycle.follow())
    recovery = Runner(capture)
    updates = Updates(backend.post)
    asyncio.create_task(run_commands(conn, app, windows, commands, recovery, updates))
    asyncio.create_task(updates.follow())
    asyncio.create_task(Startup(capture, recovery).follow())
    asyncio.create_task(recovery.follow())
    asyncio.create_task(capture.follow())
    asyncio.create_task(Inventory().follow(conn, backend.post))
    asyncio.create_task(settings.follow(conn, APP_DIR, backend.post))

    follower = Follower(conn, app, windows)
    while True:
        try:
            if not backend.alive():
                backend.failures += 1
                if backend.failures > 5:
                    stop("fbd keeps exiting; giving up", 1)
                # at once the first time: while no fbd listens, another program may take the port
                await asyncio.sleep(min(2 * (backend.failures - 1), 10))
                backend.start()
                await loop.run_in_executor(None, backend.wait_ready)
                await register(conn, run_epoch)  # fbd made a new token if it could not get its port
                await loop.run_in_executor(None, remotes.reregister)
                follower.last = None  # re-push state to the fresh process
                await updates.publish()
            if not await bounded(follower.poll(), POLL_TIMEOUT):  # costs one poll, not the loop
                log(f"poll timed out after {POLL_TIMEOUT:g} s")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            log(f"push failed: {e}")
        except Exception as e:  # keep tracking whatever happens
            log(f"error: {type(e).__name__}: {e}")
        await asyncio.sleep(POLL)


def run():
    os.umask(0o077)  # logs, state and fbd (it inherits the mask) are the user's alone (AC-07)
    iterm2.run_forever(main, retry=True)
