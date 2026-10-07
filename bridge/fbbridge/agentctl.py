# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Agents on remote hosts (AC-37, AC-38), shared by the bridge and the
`iterm-enhancer` command: the record of enabled hosts, copying an agent over ssh, and
the ssh process that carries a running agent and forwards its socket. A host is its ssh
arguments (`target`, as iTerm2's tmux gateway ran them: ["devbox.example"], ["-p", "2222", "a@vm"]);
its key is those arguments joined. Plain ssh with the user's own config; BatchMode, so a
host that wants a password or a new host key fails instead of prompting."""
import hashlib
import fcntl
import json
import os
import re
import select
import secrets
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

from .common import APP_DIR
from .sshargs import key as key_of

RECORD = APP_DIR / "agents.json"
HOME_DIR = ".iterm-enhancer"                              # on the host: bin/, logs/ (AC-38)
SSH = os.environ.get("FB_SSH", "ssh")                         # tests use a fake
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
# what `uname -sm` says → the agent's platform name (Rust's os-arch)
PLATFORMS = {("Darwin", "arm64"): "macos-aarch64", ("Darwin", "x86_64"): "macos-x86_64",
             ("Linux", "x86_64"): "linux-x86_64", ("Linux", "aarch64"): "linux-aarch64",
             ("Linux", "arm64"): "linux-aarch64"}


class AgentError(Exception):
    """A host could not be reached or prepared; the message says why."""


def load():
    """{key: {"ssh": [...], "name", "user", "platform", "agent_id"}} of enabled hosts. Records
    of Stage 8 (keyed by an alias, with "host") read as the same thing."""
    try:
        record = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}
    return {k: (e if "ssh" in e else {**e, "ssh": [k], "name": e.get("host", k)}) for k, e in record.items()}


_record_lock = threading.RLock()


@contextmanager
def record_lock():
    """Serialize the whole transaction across bridge threads and CLI processes."""
    with _record_lock:
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        with open(RECORD.with_suffix(".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


def _save(record):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORD.with_name(f".{RECORD.name}.{secrets.token_hex(6)}.tmp")
    try:
        tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
        tmp.replace(RECORD)
        _cache.update(mtime=None, record={})
    finally:
        tmp.unlink(missing_ok=True)


def save(record):
    """Replace a complete record; individual host changes use update instead."""
    with record_lock():
        _save(record)


def update(change):
    """Load, mutate and publish one record while holding the stable advisory lock."""
    with record_lock():
        record = load()
        result = change(record)
        _save(record)
        return result


_cache = {"mtime": None, "record": {}}


def entry(key):
    """The record of host `key`, or None; read again only when the file changed (this runs
    on every poll of a remote pane)."""
    with _record_lock:
        try:
            mtime = RECORD.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != _cache["mtime"]:
            _cache.update(mtime=mtime, record=load())
        return _cache["record"].get(key)


def forget(key):
    update(lambda record: record.pop(key, None))


def socket_for(key):
    """The forwarded socket on the Mac: a short name, socket paths end at 104 bytes."""
    return APP_DIR / "agents" / f"{hashlib.sha256(key.encode()).hexdigest()[:12]}.sock"


def ssh(target, command, stdin=None, timeout=60):
    """Run `command` on the host `target` names; its stdout, or AgentError with ssh's message."""
    try:
        r = subprocess.run([SSH, *SSH_OPTS, *target, command], input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise AgentError(f"{key_of(target)}: ssh timed out after {timeout:g} seconds") from e
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()
        raise AgentError(f"{key_of(target)}: {msg[-1] if msg else f'ssh exited {r.returncode}'}")
    return r.stdout.decode(errors="replace")


def probe(target):
    """(name, user, platform) of the host, or AgentError for a system without an agent."""
    out = ssh(target, "uname -sm; hostname -s 2>/dev/null || hostname; id -un").split("\n")
    system, machine = (out[0].split() + ["", ""])[:2]
    platform = PLATFORMS.get((system, machine))
    if not platform:
        raise AgentError(f"{key_of(target)}: no helper for {system} {machine} (macOS arm64/x86_64, Linux x86_64/arm64)")
    return out[1].strip().split(".")[0].lower(), out[2].strip(), platform


def install(target, binary, agent_id):
    """Copy `binary` to ~/.iterm-enhancer/bin/fbd-agent-<agent id> on the host through a
    temporary name, point bin/fbd-agent at it, keep one older version, and check it answers
    with `agent_id` (AC-38: a place the user finds; logs go to ~/.iterm-enhancer/logs).
    Never bin/fbd: a Mac host keeps its own install in the same folder (AC-40)."""
    if not re.fullmatch(r"[0-9a-f]{12}|dev", agent_id):  # it goes into a remote shell line
        raise AgentError(f"not an agent id: {agent_id!r}")
    unique = secrets.token_hex(12)
    script = (f'set -e; d="$HOME/{HOME_DIR}"; mkdir -p "$d/bin" "$d/logs"; chmod 700 "$d"; '
              f'tmp="$d/bin/.fbd-agent.{unique}.tmp"; link="$d/bin/.fbd-agent.{unique}.new"; '
              f'trap \'rm -f "$tmp" "$link"\' EXIT; '
              f'cat > "$tmp"; chmod 755 "$tmp"; '
              f'got=$("$tmp" --agent-id); [ "$got" = "{agent_id}" ] || {{ echo "helper answers $got, expected {agent_id}" >&2; exit 1; }}; '
              f'mv -f "$tmp" "$d/bin/fbd-agent-{agent_id}"; '
              f'ln -s "fbd-agent-{agent_id}" "$link"; mv -f "$link" "$d/bin/fbd-agent"; '
              f'ls -1t "$d/bin" | grep "^fbd-agent-" | grep -v "^fbd-agent-{agent_id}$" | tail -n +2 | while read -r f; do rm -f "$d/bin/$f"; done; '
              f'"$d/bin/fbd-agent-{agent_id}" --agent-id')
    got = ssh(target, script, stdin=Path(binary).read_bytes(), timeout=300).strip()
    if got != agent_id:
        raise AgentError(f"{key_of(target)}: the copied helper says {got or 'nothing'}, expected {agent_id}")


def remove(target):
    """Take the agent, its versions and its log off the host, and the folders once empty;
    a Mac host's own install in the same root stays (AC-40)."""
    ssh(target, f'd="$HOME/{HOME_DIR}"; rm -f "$d/bin/fbd-agent" "$d/bin/fbd-agent-"* "$d/bin/.fbd-agent."* "$d/logs/agent.log"*; '
                f'rmdir "$d/bin" "$d/logs" "$d" 2>/dev/null; true')


class Tunnel:
    """One ssh process to the host that runs the agent and forwards its socket to the Mac.
    The token goes on the agent's stdin; when this process ends, so does the agent. With
    `agent_id` it runs that version's file when the host has it, else whatever bin/fbd-agent
    points at: two Macs of different builds on one host each run their own, and neither
    copies its helper over the other's on every connect (AC-42)."""

    def __init__(self, target, local_socket, agent_id=None):
        self.target, self.local = list(target), Path(local_socket)
        self.own = agent_id if agent_id and re.fullmatch(r"[0-9a-f]{12}|dev", agent_id) else None  # goes into a shell line
        self.token = secrets.token_hex(16)
        self.proc = None
        self.reader = None
        self.reader_stop = threading.Event()
        self.ready = None  # (agent_id, host, user, platform) once the agent said so

    def start(self, timeout=20):
        self.local.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.local.exists() or self.local.is_symlink():
            self.local.unlink()
        remote = f"/tmp/fbd-{secrets.token_hex(6)}/s"
        b = f'"$HOME/{HOME_DIR}/bin/fbd-agent'
        pick = f'f={b}-{self.own}"; [ -x "$f" ] || f={b}"; ' if self.own else f"f={b}\"; "
        self.proc = subprocess.Popen(
            [SSH, *SSH_OPTS, "-T", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
             "-L", f"{self.local}:{remote}", *self.target,
             pick + f'FB_LOG=info exec "$f" --agent --socket {remote} --log "$HOME/{HOME_DIR}/logs/agent.log"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.proc.stdin.write((self.token + "\n").encode())
        self.proc.stdin.flush()
        found = []
        self.reader_stop.clear()
        os.set_blocking(self.proc.stdout.fileno(), False)

        def read():  # a login shell may print a banner before the agent's line
            pending, lines = b"", 0
            while not self.reader_stop.is_set() and lines < 50:
                if not select.select([self.proc.stdout], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(self.proc.stdout.fileno(), 8192)
                except BlockingIOError:
                    continue
                if chunk:
                    pending += chunk
                elif pending:
                    pending += b"\n"
                else:
                    return found.append([])
                while b"\n" in pending:
                    if lines >= 50:
                        return found.append([])
                    line, _, pending = pending.partition(b"\n")
                    words = line.decode(errors="replace").split()
                    lines += 1
                    if not words or (len(words) == 6 and words[:2] == ["fbd-agent", "ready"]):
                        return found.append(words)
                if len(pending) > 8192 or not chunk:
                    return found.append([])
        reader = threading.Thread(target=read, daemon=True)
        self.reader = reader
        reader.start()
        reader.join(timeout)
        if found and found[0]:
            self.ready = tuple(found[0][2:])
            return self.ready
        err = self.stop().strip().splitlines()
        raise AgentError(f"{key_of(self.target)}: {err[-1] if err else 'the helper did not start'}")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        """Close the connection: the agent's stdin closes and it exits on the host."""
        self.reader_stop.set()
        if self.proc and self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(1)
                except subprocess.TimeoutExpired:
                    threading.Thread(target=self.proc.wait, daemon=True).start()
        if self.reader:
            self.reader.join(0.3)
        error = ""
        if self.proc:
            if self.proc.stderr and not self.proc.stderr.closed:
                fd = self.proc.stderr.fileno()
                os.set_blocking(fd, False)
                chunks, remaining = [], 8192
                while remaining:
                    try:
                        chunk = os.read(fd, remaining)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                error = b"".join(chunks).decode(errors="replace")
            proc, reader = self.proc, self.reader

            def close_streams():
                if reader and reader.is_alive():
                    reader.join()
                for stream in (proc.stdout, proc.stderr):
                    if stream and not stream.closed:
                        stream.close()
            if reader and reader.is_alive():
                threading.Thread(target=close_streams, daemon=True).start()
            else:
                close_streams()
        if self.local.exists():
            self.local.unlink()
        return error
