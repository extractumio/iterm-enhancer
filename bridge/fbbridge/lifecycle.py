# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""One bridge per user, and its exit with iTerm2 (AC-30). iTerm2 does not signal its
scripts on quit, it only closes the API socket, so the bridge must notice by itself."""
import asyncio
import fcntl
import os
import signal
import threading
import time

from .common import log
from .procinfo import command_of, proc_start

MARK = "fb_bridge.py"  # in the command line of every bridge


class LockError(Exception):
    pass


def _try_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _wait_lock(fd, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _try_lock(fd):
            return True
        time.sleep(0.1)
    return False


def _holder(fd):
    """(pid, command) written in the lock file by its holder; (0, "") if unreadable."""
    text = os.pread(fd, 32, 0).decode(errors="replace").strip()
    pid = int(text) if text.isdigit() else 0
    return pid, command_of(pid) if pid > 1 and pid != os.getpid() else ""


def _signal(pid, sig):
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass  # exited on its own: the lock follows


def take_lock(path, grace=3.0):
    """Hold `path` for the rest of this process, stopping the bridge that holds it (newest
    wins: macOS runs one iTerm2, and the bridge it launched last is the connected one).
    Returns the fd; the kernel releases the lock on any exit, SIGKILL included."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)  # not inherited by fbd
    if not _try_lock(fd):
        pid, cmd = _holder(fd)
        # a holder that is exiting, or has not written its pid yet, is judged a moment later
        if MARK not in cmd and not _wait_lock(fd, 0.5):
            pid, cmd = _holder(fd)
            if MARK not in cmd:
                os.close(fd)
                raise LockError(f"{path.name} held by pid {pid} ({cmd or 'unknown'}), not a bridge")
        if MARK in cmd:
            _signal(pid, signal.SIGTERM)
            if not _wait_lock(fd, grace):
                _signal(pid, signal.SIGKILL)
                if not _wait_lock(fd, 2.0):
                    os.close(fd)
                    raise LockError(f"bridge pid {pid} keeps {path.name} after SIGKILL")
            log(f"took over from bridge pid {pid}")
    os.ftruncate(fd, 0)
    os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
    return fd


def connection_closed(ws):
    """The `iterm2` module's websocket: the legacy client has `closed`, the new one `state`."""
    if ws is None:
        return True
    closed = getattr(ws, "closed", None)
    return closed if closed is not None else getattr(getattr(ws, "state", None), "name", "") == "CLOSED"


def watch(on_exit, closed, iterm=None, every=1.0):
    """Call on_exit(reason) once the iTerm2 process `iterm` = (pid, start) is gone or
    closed() says the API connection is. A thread, so a stuck event loop cannot stop it."""
    def run():
        while True:
            time.sleep(every)
            try:
                if iterm and proc_start(iterm[0]) != iterm[1]:
                    return on_exit(f"iTerm2 pid {iterm[0]} gone")
                if closed():
                    return on_exit("iTerm2 API connection closed")
            except Exception as e:  # a broken check must not end the watch silently
                log(f"watchdog: {type(e).__name__}: {e}")
    t = threading.Thread(target=run, name="watchdog", daemon=True)
    t.start()
    return t


async def bounded(coro, seconds):
    """Run `coro` for at most `seconds`: False if it was abandoned. The `iterm2` module never
    fails a call whose answer does not come, so every poll needs its own limit."""
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait([task], timeout=seconds)
    if not done:
        task.cancel()
        return False
    task.result()  # the poll's own errors reach the caller
    return True
