# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Enabling a remote host (AC-38), for the panel's Enable button (through the bridge) and
the `iterm-filebrowser hosts enable` command: read the host's platform, copy the agent the
running build carries for it, check it answers through a trial tunnel, record the host."""
import http.client
import socket

from . import agentctl
from .common import BUILD_DIR
from .sshargs import key as key_of

LOCAL_AGENT_ID = (BUILD_DIR / "AGENT_ID").read_text().strip() if (BUILD_DIR / "AGENT_ID").is_file() else None


def binary_for(platform):
    """The agent the running build carries for `platform`, or AgentError."""
    if platform not in agentctl.PLATFORMS.values():
        raise agentctl.AgentError(f"this build has no helper for {platform}")
    path = BUILD_DIR / "agents" / platform / "fbd"
    if not path.is_file():
        raise agentctl.AgentError(f"this build has no helper for {platform}")
    return path


class UnixHTTP(http.client.HTTPConnection):
    """HTTP over the forwarded Unix socket, as the Mac's fbd talks to an agent."""

    def __init__(self, path):
        super().__init__("fbd-agent", timeout=10)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(str(self.path))


def trial(target):
    """Start the agent through ssh as the bridge does, ask it for its health, stop it."""
    t = agentctl.Tunnel(target, agentctl.socket_for(key_of(target) + " trial"))
    try:
        ready = t.start()
        c = UnixHTTP(t.local)
        c.request("GET", "/api/health", headers={"Host": "fbd-agent", "X-FB-Token": t.token})
        status = c.getresponse().status
        if status != 200:
            raise agentctl.AgentError(f"{key_of(target)}: the helper answered {status} through the tunnel")
        return ready
    finally:
        t.stop()


def enable(target, binary_for=binary_for, agent_id=None, active=lambda: True):
    """Make the host `target` reaches ready and record it; returns its record entry."""
    agent_id = agent_id or LOCAL_AGENT_ID
    if not agent_id:
        raise agentctl.AgentError("this build does not know its agent id (install it with make install)")
    name, user, platform = agentctl.probe(target)
    agentctl.install(target, binary_for(platform), agent_id)
    got = trial(target)[0]
    if got != agent_id:
        raise agentctl.AgentError(f"{key_of(target)}: the helper answers as {got}, expected {agent_id}")
    entry = {"ssh": list(target), "name": name, "user": user, "platform": platform, "agent_id": agent_id}
    def publish(record):
        if active():
            record[key_of(target)] = entry
    agentctl.update(publish)
    return entry


def remove(target):
    """Take the helper off the host and forget it."""
    agentctl.forget(key_of(target))
    agentctl.remove(target)
