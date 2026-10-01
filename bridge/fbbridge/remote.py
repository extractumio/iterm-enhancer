# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The bridge's side of remote hosts (AC-37): when a focused tmux -CC pane lives on a
host that has an agent (agents.json, written by `make agent`), keep an ssh connection
carrying that agent, register its socket with fbd, bring an outdated agent up to the
running build, and reconnect with back-off. Threads, so ssh never blocks the poll loop."""
import threading
import time

from . import agentctl
from .common import BUILD_DIR, log

BACKOFF_MAX = 30.0
LOCAL_AGENT_ID = (BUILD_DIR / "AGENT_ID").read_text().strip() if (BUILD_DIR / "AGENT_ID").is_file() else None


class Remotes:
    def __init__(self, post):
        self.post = post            # backend.post: (path, body)
        self.lock = threading.Lock()
        self.hosts = {}             # host → {"status", "note", "retry_at", "backoff", "tunnel"}

    def recorded(self, host):
        return agentctl.alias_for(host) is not None

    def status(self, host):
        """(connected, note) for a remote pane on `host`; starts connecting when due."""
        alias = agentctl.alias_for(host)
        if not alias:
            return False, f"remote host {host} · make agent HOST=<ssh alias> to browse it"
        with self.lock:
            h = self.hosts.setdefault(host, {"status": "new", "note": f"connecting to {host}…", "retry_at": 0.0, "backoff": 1.0})
            if h["status"] == "up":
                return True, f"{host} (agent)"
            if h["status"] != "connecting" and time.monotonic() >= h["retry_at"]:
                h.update(status="connecting", note=f"connecting to {host}…")
                threading.Thread(target=self._run, args=(host, alias), name=f"agent-{alias}", daemon=True).start()
            return False, h["note"]

    def _set(self, host, **kw):
        with self.lock:
            self.hosts[host].update(kw)

    def _connect(self, alias):
        t = agentctl.Tunnel(alias, agentctl.socket_for(alias))
        agent_id, _, _, platform = t.start()
        if LOCAL_AGENT_ID and agent_id != LOCAL_AGENT_ID:  # bring it up to the running build
            t.stop()
            binary = BUILD_DIR / "agents" / platform / "fbd"
            if not binary.is_file():
                raise agentctl.AgentError(f"agent outdated · make agent HOST={alias}")
            log(f"agent on {alias}: {agent_id} → {LOCAL_AGENT_ID}")
            agentctl.install(alias, binary, LOCAL_AGENT_ID)
            record = agentctl.load()
            record.setdefault(alias, {})["agent_id"] = LOCAL_AGENT_ID
            agentctl.save(record)
            t = agentctl.Tunnel(alias, agentctl.socket_for(alias))
            t.start()
        return t

    def _run(self, host, alias):
        try:
            t = self._connect(alias)
        except (agentctl.AgentError, OSError) as e:
            return self._failed(host, str(e))
        try:
            self.post("/internal/remote", {"host": host, "socket": str(t.local), "token": t.token})
        except OSError as e:  # fbd restarting: drop this connection, the next try registers
            t.stop()
            return self._failed(host, f"fbd did not take {host}: {e}")
        self._set(host, status="up", note=f"{host} (agent)", backoff=1.0, tunnel=t)
        log(f"agent on {alias} connected")
        t.proc.wait()                                   # until the connection ends
        try:
            self.post("/internal/remote", {"host": host, "socket": None, "token": None})
        except OSError:
            pass
        t.stop()
        self._failed(host, f"{alias}: connection closed")

    def _failed(self, host, why):
        log(f"agent: {why}")
        with self.lock:
            h = self.hosts[host]
            h.update(status="down", note=why, retry_at=time.monotonic() + h["backoff"], backoff=min(h["backoff"] * 2, BACKOFF_MAX))

    def reregister(self):
        """A restarted fbd knows no hosts: tell it the connected ones again."""
        with self.lock:
            live = [(h, d["tunnel"]) for h, d in self.hosts.items() if d["status"] == "up" and d.get("tunnel")]
        for host, t in live:
            try:
                self.post("/internal/remote", {"host": host, "socket": str(t.local), "token": t.token})
            except OSError as e:
                log(f"agent: fbd did not take {host}: {e}")

    def stop(self):
        with self.lock:
            tunnels = [h.get("tunnel") for h in self.hosts.values()]
        for t in tunnels:
            if t:
                t.stop()
