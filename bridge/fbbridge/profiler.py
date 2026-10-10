# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Where the bridge spends its time (AC-55), on request: `kill -USR1 <bridge pid>` samples the
main thread's Python stack for SECONDS and writes the busiest functions and stacks to
profile-<time>.txt in the log folder. Nothing runs until then."""
import collections
import os
import signal
import sys
import threading
import time

from . import common
from .common import log

SECONDS = 15
EVERY = 0.005
TOP = 25


def _sample(main_id, out):
    leaf, stacks, n = collections.Counter(), collections.Counter(), 0
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        frame = sys._current_frames().get(main_id)
        if frame is not None:
            n += 1
            names = []                               # formatted once at the end, not per sample
            while frame is not None and len(names) < 6:
                names.append((frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno))
                frame = frame.f_back
            leaf[names[0]] += 1
            stacks[tuple(names)] += 1
        time.sleep(EVERY)
    with open(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:   # 0600, as the trace
        f.write(f"{n} samples of the main thread over {SECONDS} s (a waiting loop sits in select)\n\nfunctions:\n")
        f.writelines(f"{c:6d} {100 * c / max(n, 1):5.1f}%  {_name(k)}\n" for k, c in leaf.most_common(TOP))
        f.write("\nstacks:\n")
        f.writelines(f"{c:6d} {100 * c / max(n, 1):5.1f}%  {' < '.join(map(_name, k))}\n" for k, c in stacks.most_common(TOP))
    log(f"profile written: {out}")


def _name(where):
    path, func, line = where
    return f"{os.path.basename(path)}:{func}:{line}"


def install():
    main_id = threading.main_thread().ident

    def start(*_):
        common.LOG_DIR.mkdir(parents=True, exist_ok=True)
        out = common.LOG_DIR / time.strftime("profile-%Y%m%d-%H%M%S.txt")
        threading.Thread(target=_sample, args=(main_id, out), daemon=True).start()
    signal.signal(signal.SIGUSR1, start)
