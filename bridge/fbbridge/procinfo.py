# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Process facts from libproc (cwd, parent, name, foreground group) without forking."""
import ctypes
import os
import shutil
import subprocess
import time

from .common import SHELLS

_libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
_PROC_PIDTBSDINFO, _PROC_PIDVNODEPATHINFO = 3, 9
_VNODE_INFO_SIZE, _MAXPATHLEN = 152, 1024


def proc_cwd(pid):
    buf = ctypes.create_string_buffer(2 * (_VNODE_INFO_SIZE + _MAXPATHLEN))
    if _libproc.proc_pidinfo(pid, _PROC_PIDVNODEPATHINFO, 0, buf, len(buf)) <= 0:
        return None
    raw = buf.raw[_VNODE_INFO_SIZE:_VNODE_INFO_SIZE + _MAXPATHLEN]
    return raw.split(b"\0", 1)[0].decode(errors="replace") or None


def _bsdinfo(pid):
    """Raw struct proc_bsdinfo (136 bytes) or None; fields read below by offset."""
    buf = ctypes.create_string_buffer(136)
    if not pid or _libproc.proc_pidinfo(pid, _PROC_PIDTBSDINFO, 0, buf, len(buf)) <= 0:
        return None
    return buf.raw


def _u32(raw, offset):
    return int.from_bytes(raw[offset:offset + 4], "little") if raw else None


def proc_ppid(pid):
    return _u32(_bsdinfo(pid), 16)  # pbi_ppid


def proc_name(pid):
    buf = ctypes.create_string_buffer(256)
    n = _libproc.proc_name(pid, buf, len(buf))
    return buf.raw[:n].decode(errors="replace").lstrip("-") if n > 0 else None


def proc_path(pid):
    buf = ctypes.create_string_buffer(4096)
    n = _libproc.proc_pidpath(pid, buf, len(buf))
    return buf.raw[:n].decode(errors="replace") if n > 0 else None


def proc_children(pid):
    buf = (ctypes.c_int * 256)()
    n = _libproc.proc_listchildpids(pid, buf, ctypes.sizeof(buf))
    return list(buf[:max(n, 0)])


def foreground_pid(pid):
    """Foreground process group of `pid`'s terminal (e_tpgid), straight from the kernel:
    iTerm2's jobPid lags ~2 s, and tcgetpgrp only works on our own terminal."""
    return _u32(_bsdinfo(pid), 112) or None


def shell_of(job_pid, root_pid):
    """Nearest shell at or above the foreground job, so `vim` after `:cd` or a nested
    bash reports the shell's cwd, not the job's."""
    pid = job_pid
    for _ in range(16):
        if not pid or pid <= 1:
            break
        if proc_name(pid) in SHELLS:
            return pid
        if pid == root_pid:
            break
        pid = proc_ppid(pid)
    for child in proc_children(root_pid) if root_pid else []:  # root is usually `login`
        if proc_name(child) in SHELLS:
            return child
    return job_pid


_tmux_args = {}


def tmux_command(client_pid):
    """The client's own tmux binary (AutoLaunch has no Homebrew PATH) plus the -L/-S
    flags it was started with (cached per pid)."""
    if client_pid not in _tmux_args:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(client_pid)],
                             capture_output=True, text=True).stdout.split()
        args = []
        for i, a in enumerate(out[:-1]):
            if a in ("-L", "-S"):
                args = [a, out[i + 1]]
                break
        exe = proc_path(client_pid) or shutil.which("tmux") or "/opt/homebrew/bin/tmux"
        _tmux_args[client_pid] = [exe, *args]
    return _tmux_args[client_pid]


def proc_start(pid):
    """Start time (epoch seconds) of `pid`, or None: with the pid it names one process."""
    raw = _bsdinfo(pid)
    return int.from_bytes(raw[120:128], "little") or None if raw else None  # pbi_start_tvsec


def iterm_process():
    """(pid, start) of the iTerm2 process that runs us, or None (started elsewhere)."""
    pid = os.getpid()
    for _ in range(8):
        pid = proc_ppid(pid)
        if not pid or pid <= 1:
            return None
        if proc_name(pid) == "iTerm2":
            return pid, proc_start(pid)
    return None


def iterm_uptime():
    """Seconds since the iTerm2 process that runs us started (None if not found)."""
    p = iterm_process()
    return time.time() - p[1] if p and p[1] else None
