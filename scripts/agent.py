#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Make a host ready for the Files panel (AC-38): `make agent HOST=<ssh alias>`.

Reads the host's platform over ssh, builds the agent for it (scripts/agents.py), copies it
to the host, opens a trial tunnel to check the agent answers through a forwarded socket,
and records the host in agents.json, so the bridge connects it whenever a tmux -CC pane
of that host is focused, and keeps its agent up to date.

    python3 scripts/agent.py <alias>
"""
import http.client
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bridge"))
sys.path.insert(0, str(REPO / "scripts"))

import agents  # noqa: E402
from fbbridge import agentctl  # noqa: E402


class UnixHTTP(http.client.HTTPConnection):
    """HTTP over the forwarded Unix socket, as the Mac's fbd talks to the agent."""

    def __init__(self, path):
        super().__init__("fbd-agent", timeout=10)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect(str(self.path))


def trial(alias):
    """Start the agent through ssh like the bridge does, ask it for its health, stop it."""
    t = agentctl.Tunnel(alias, agentctl.socket_for(alias + ".trial"))
    try:
        ready = t.start()
        c = UnixHTTP(t.local)
        c.request("GET", "/api/health", headers={"Host": "fbd-agent", "X-FB-Token": t.token})
        r = c.getresponse()
        if r.status != 200:
            raise agentctl.AgentError(f"{alias}: the agent answered {r.status} through the tunnel")
        return ready
    finally:
        t.stop()


def prepare(alias, build=agents.build):
    host, user, platform = agentctl.probe(alias)
    record = agentctl.load()
    other = next((a for a, e in record.items() if e.get("host") == host and a != alias), None)
    if other:
        raise agentctl.AgentError(f"{alias} reports host name {host}, already used by {other}: "
                                  "set a distinct host name on one of them")
    aid = agents.agent_id()
    binary = build([platform])[platform]
    agentctl.install(alias, binary, aid)
    got_id = trial(alias)[0]
    if got_id != aid:
        raise agentctl.AgentError(f"{alias}: the agent answers as {got_id}, expected {aid}")
    record[alias] = {"host": host, "user": user, "platform": platform, "agent_id": aid}
    agentctl.save(record)
    return f"{alias} ready ({platform}, host {host}, user {user}, agent {aid})"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0] or argv[0].startswith("-"):
        sys.exit("usage: make agent HOST=<ssh alias>")
    try:
        print(prepare(argv[0]))
    except (agentctl.AgentError, agents.Failed, OSError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
