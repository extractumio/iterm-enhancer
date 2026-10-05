# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Darwin root-process exit evidence; status is ephemeral and never persisted."""
import os
import select
import stat

from . import procinfo

NOTE_EXITSTATUS = 0x04000000  # Darwin sys/event.h; absent from Python's select constants.


async def root_identity(session):
    try:
        pid = await session.async_get_variable("pid")
        if isinstance(pid, bool) or not isinstance(pid, (int, str)) or int(pid) <= 0:
            return None
        pid = int(pid)
        tty = await session.async_get_variable("tty")
        if not isinstance(tty, str) or not tty.startswith("/dev/"):
            return None
        device = os.stat(tty)
        started = procinfo.proc_start(pid)
        if not started or not stat.S_ISCHR(device.st_mode) or procinfo.proc_terminal(pid) != (device.st_rdev & 0xffffffff):
            return None
        return pid, started, device.st_rdev
    except Exception:
        return None


class ExitWatch:
    def __init__(self):
        self.queue = select.kqueue()
        self.sessions = {}

    async def enroll(self, session, identity):
        pid = identity[0]
        event = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                             flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                             fflags=select.KQ_NOTE_EXIT | NOTE_EXITSTATUS)
        try:
            self.queue.control([event], 0, 0)
            # A reused PID or changed controlling terminal cannot own the watch.
            if await root_identity(session) != identity:
                self.remove(pid)
                return False
            self.sessions[pid] = session.session_id, identity
            return True
        except OSError:
            return False

    def remove(self, pid):
        self.sessions.pop(pid, None)
        try:
            self.queue.control([select.kevent(pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_DELETE)], 0, 0)
        except OSError:
            pass

    def drain(self):
        completed = []
        for event in self.queue.control(None, 2048, 0):
            observed = self.sessions.pop(event.ident, None)
            if observed and event.fflags & NOTE_EXITSTATUS and os.WIFEXITED(event.data):
                completed.append((*observed, os.WEXITSTATUS(event.data) == 0))
        return completed

    def close(self):
        self.queue.close()
