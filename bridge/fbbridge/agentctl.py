# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Agents on remote hosts (AC-37, AC-38), shared by the bridge and the
`iterm-filebrowser` command: the record of enabled hosts, copying an agent over ssh, and
the ssh process that carries a running agent and forwards its socket. A host is its ssh
arguments (`target`, as iTerm2's tmux gateway ran them: ["ai4"], ["-p", "2222", "a@vm"]);
its key is those arguments joined. Plain ssh with the user's own config; BatchMode, so a
host that wants a password or a new host key fails instead of prompting."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
from pathlib import Path

from .common import APP_DIR
from .sshargs import key as key_of

RECORD = APP_DIR / "agents.json"
HOME_DIR = ".iterm-filebrowser"                              # on the host: bin/, logs/ (AC-38)
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


def save(record):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORD.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
    tmp.replace(RECORD)


_cache = {"mtime": None, "record": {}}


def entry(key):
    """The record of host `key`, or None; read again only when the file changed (this runs
    on every poll of a remote pane)."""
    try:
        mtime = RECORD.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime != _cache["mtime"]:
        _cache.update(mtime=mtime, record=load())
    return _cache["record"].get(key)


def forget(key):
    record = load()
    if record.pop(key, None) is not None:
        save(record)


def socket_for(key):
    """The forwarded socket on the Mac: a short name, socket paths end at 104 bytes."""
    return APP_DIR / "agents" / f"{hashlib.sha256(key.encode()).hexdigest()[:12]}.sock"


def ssh(target, command, stdin=None, timeout=60):
    """Run `command` on the host `target` names; its stdout, or AgentError with ssh's message."""
    r = subprocess.run([SSH, *SSH_OPTS, *target, command], input=stdin, capture_output=True, timeout=timeout)
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
    """Copy `binary` to ~/.iterm-filebrowser/bin/fbd-agent-<agent id> on the host through a
    temporary name, point bin/fbd-agent at it, keep one older version, and check it answers
    with `agent_id` (AC-38: a place the user finds; logs go to ~/.iterm-filebrowser/logs).
    Never bin/fbd: a Mac host keeps its own install in the same folder (AC-40)."""
    if not re.fullmatch(r"[0-9a-f]{12}|dev", agent_id):  # it goes into a remote shell line
        raise AgentError(f"not an agent id: {agent_id!r}")
    script = (f'set -e; d="$HOME/{HOME_DIR}"; mkdir -p "$d/bin" "$d/logs"; chmod 700 "$d"; '
              f'cat > "$d/bin/.fbd-agent.tmp"; chmod 755 "$d/bin/.fbd-agent.tmp"; '
              f'mv -f "$d/bin/.fbd-agent.tmp" "$d/bin/fbd-agent-{agent_id}"; '
              f'ln -sfn "fbd-agent-{agent_id}" "$d/bin/.fbd-agent.new"; mv -f "$d/bin/.fbd-agent.new" "$d/bin/fbd-agent"; '
              f'ls -1t "$d/bin" | grep "^fbd-agent-" | grep -v "^fbd-agent-{agent_id}$" | tail -n +2 | while read -r f; do rm -f "$d/bin/$f"; done; '
              f'rm -rf "$HOME/.local/lib/iterm-filebrowser/agent"; '   # Stage 8's helper only (a Mac host keeps its own install)
              f'"$d/bin/fbd-agent" --agent-id')
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

        def read():  # a login shell may print a banner before the agent's line
            for _ in range(50):
                words = self.proc.stdout.readline().decode(errors="replace").split()
                if not words or (len(words) == 6 and words[:2] == ["fbd-agent", "ready"]):
                    return found.append(words)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout)
        if found and found[0]:
            self.ready = tuple(found[0][2:])
            return self.ready
        self.stop()
        err = self.proc.stderr.read().decode(errors="replace").strip().splitlines() if self.proc.stderr else []
        raise AgentError(f"{key_of(self.target)}: {err[-1] if err else 'the helper did not start'}")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        """Close the connection: the agent's stdin closes and it exits on the host."""
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.local.exists():
            self.local.unlink()
