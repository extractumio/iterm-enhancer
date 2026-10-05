# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Record proven normal app exits before the watchdog stops the private backend."""
import os
import select
import threading

from .common import log
from .procinfo import proc_start
from .recovery_exit import NOTE_EXITSTATUS


class QuitWatch:
    def __init__(self, process, record):
        self.process, self.record = process, record
        self.queue = None
        self.completed = None
        self.recorded = False
        self.lock = threading.RLock()
        if not process or not process[1]:
            return
        pid, started = process
        try:
            if proc_start(pid) != started:
                return
            self.queue = select.kqueue()
            event = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                                 flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                                 fflags=select.KQ_NOTE_EXIT | NOTE_EXITSTATUS)
            self.queue.control([event], 0, 0)
            if proc_start(pid) != started:
                self.close()  # enrollment across a PID change cannot prove an exit
        except OSError:
            self.close()
            log("Normal application exit tracking unavailable; unknown exits remain recoverable")

    def finish(self, timeout=0):
        """A queued zero status, never mere PID/API loss, authorizes durable marking."""
        with self.lock:
            if self.recorded:
                return True
            if self.queue is None:
                return False
            if self.completed is None:
                try:
                    events = self.queue.control(None, 1, timeout)
                except OSError:
                    return False
                for event in events:
                    if event.ident == self.process[0]:
                        self.completed = bool(event.fflags & NOTE_EXITSTATUS and
                                              os.WIFEXITED(event.data) and os.WEXITSTATUS(event.data) == 0)
            if not self.completed:
                return False
            try:
                self.record()
            except Exception as e:
                # Cached kernel evidence permits a later bounded retry; no file is
                # written by the bridge and a lost acknowledgement stays unknown.
                log(f"Normal application exit was not recorded ({type(e).__name__}); recovery history preserved")
                return False
            self.recorded = True
            return True

    def close(self):
        with self.lock:
            if self.queue is not None:
                self.queue.close()
                self.queue = None
