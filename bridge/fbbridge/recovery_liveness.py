# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Fresh positive proof of an ended terminal; metadata failures preserve identities."""
import os
import stat

from .procinfo import proc_terminal


async def session_state(session):
    try:
        if await session.async_get_variable("tmuxRole") == "client":
            return "live"
        pid = await session.async_get_variable("pid")
        if isinstance(pid, bool) or not isinstance(pid, (int, str)):
            return "unknown"
        pid = int(pid)
        if pid <= 0:
            return "unknown"
        os.kill(pid, 0)
        tty = await session.async_get_variable("tty")
        if not isinstance(tty, str) or not tty.startswith("/dev/"):
            return "unknown"
        device = os.stat(tty)
        if not stat.S_ISCHR(device.st_mode) or proc_terminal(pid) != (device.st_rdev & 0xffffffff):
            return "unknown"
        return "live"
    except ProcessLookupError:
        return "ended"
    except Exception:
        return "unknown"
