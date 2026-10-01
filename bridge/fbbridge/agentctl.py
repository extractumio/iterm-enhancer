# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Agents on remote hosts (AC-37, AC-38), shared by the bridge and `make agent`: the
record of prepared hosts, copying an agent over ssh, and the ssh process that carries a
running agent and forwards its socket. Plain ssh with the user's own config; BatchMode,
so a host that wants a password or a new host key fails instead of prompting."""
import hashlib
import json
import os
import re
import secrets
import subprocess
import threading
from pathlib import Path

from .common import APP_DIR

RECORD = APP_DIR / "agents.json"
REMOTE_LIB = ".local/lib/iterm-filebrowser/agent"           # under the remote $HOME
SSH = os.environ.get("FB_SSH", "ssh")                         # tests use a fake
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
# what `uname -sm` says → the agent's platform name (Rust's os-arch)
PLATFORMS = {("Darwin", "arm64"): "macos-aarch64", ("Darwin", "x86_64"): "macos-x86_64",
             ("Linux", "x86_64"): "linux-x86_64", ("Linux", "aarch64"): "linux-aarch64",
             ("Linux", "arm64"): "linux-aarch64"}


class AgentError(Exception):
    """A host could not be reached or prepared; the message says why."""


def load():
    """{alias: {"host", "user", "platform", "agent_id"}} of prepared hosts."""
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def save(record):
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORD.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True))
    tmp.replace(RECORD)


_cache = {"mtime": None, "record": {}}


def alias_for(host):
    """The alias whose agent serves `host` (tmux's #{host}, short and lower case); the record
    is read again only when it changed (this runs on every poll of a remote pane)."""
    try:
        mtime = RECORD.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime != _cache["mtime"]:
        _cache.update(mtime=mtime, record=load())
    return next((a for a, e in _cache["record"].items() if e.get("host") == host), None)


def socket_for(alias):
    """The forwarded socket on the Mac: a short name, socket paths end at 104 bytes."""
    return APP_DIR / "agents" / f"{hashlib.sha256(alias.encode()).hexdigest()[:12]}.sock"


def ssh(alias, command, stdin=None, timeout=60):
    """Run `command` on `alias`; its stdout, or AgentError with ssh's own message."""
    r = subprocess.run([SSH, *SSH_OPTS, alias, command], input=stdin, capture_output=True, timeout=timeout)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()
        raise AgentError(f"{alias}: {msg[-1] if msg else f'ssh exited {r.returncode}'}")
    return r.stdout.decode(errors="replace")


def probe(alias):
    """(host, user, platform) of `alias`, or AgentError for a platform without an agent."""
    out = ssh(alias, "uname -sm; hostname -s 2>/dev/null || hostname; id -un").split("\n")
    system, machine = (out[0].split() + ["", ""])[:2]
    platform = PLATFORMS.get((system, machine))
    if not platform:
        raise AgentError(f"{alias}: no agent for {system} {machine} (macOS arm64/x86_64, Linux x86_64/arm64)")
    return out[1].strip().split(".")[0].lower(), out[2].strip(), platform


def install(alias, binary, agent_id):
    """Copy `binary` to <remote lib>/<agent id>/fbd through a temporary name, link `current`,
    keep two versions, and check it answers with `agent_id`."""
    if not re.fullmatch(r"[0-9a-f]{12}|dev", agent_id):  # it goes into a remote shell line
        raise AgentError(f"not an agent id: {agent_id!r}")
    d = f"$HOME/{REMOTE_LIB}"
    script = (f'set -e; d="{d}"; mkdir -p "$d/{agent_id}"; cat > "$d/{agent_id}/.fbd.tmp"; '
              f'chmod 755 "$d/{agent_id}/.fbd.tmp"; mv -f "$d/{agent_id}/.fbd.tmp" "$d/{agent_id}/fbd"; '
              f'ln -sfn "{agent_id}" "$d/.current.new" && mv -fT "$d/.current.new" "$d/current" 2>/dev/null '
              f'|| {{ rm -f "$d/current"; mv -f "$d/.current.new" "$d/current"; }}; '
              f'cd "$d" && ls -1t | grep -v -e "^current$" -e "^{agent_id}$" | tail -n +2 | xargs rm -rf; '
              f'"$d/current/fbd" --agent-id')
    got = ssh(alias, script, stdin=Path(binary).read_bytes(), timeout=300).strip()
    if got != agent_id:
        raise AgentError(f"{alias}: the copied agent says {got or 'nothing'}, expected {agent_id}")


class Tunnel:
    """One ssh process to `alias` that runs the agent and forwards its socket to the Mac.
    The token goes on the agent's stdin; when this process ends, so does the agent."""

    def __init__(self, alias, local_socket):
        self.alias, self.local = alias, Path(local_socket)
        self.token = secrets.token_hex(16)
        self.proc = None
        self.ready = None  # (agent_id, host, user, platform) once the agent said so

    def start(self, timeout=20):
        self.local.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.local.exists() or self.local.is_symlink():
            self.local.unlink()
        remote = f"/tmp/fbd-{secrets.token_hex(6)}/s"
        self.proc = subprocess.Popen(
            [SSH, *SSH_OPTS, "-T", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
             "-L", f"{self.local}:{remote}", self.alias, f'"$HOME/{REMOTE_LIB}/current/fbd" --agent --socket {remote}'],
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
        raise AgentError(f"{self.alias}: {err[-1] if err else 'the agent did not start'}")

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
