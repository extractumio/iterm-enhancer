# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The bridge's side of remote hosts (AC-37, AC-38). A focused tmux -CC pane names its host
by the ssh arguments of its gateway (`key`). An enabled host (agents.json) gets one ssh
connection carrying its agent, registered with fbd, its agent brought up to the running
build, reconnected with back-off. Any other host is offered once per bridge run: Enable
copies the agent over that ssh (hosts.enable), "Not now" hides the offer until the bridge
restarts. Threads, so ssh never blocks the poll loop."""
import threading
import time

from . import agentctl, hosts
from .common import BUILD_DIR, log

BACKOFF_MAX = 30.0


class Remotes:
    def __init__(self, post):
        self.post = post            # backend.post: (path, body)
        self.lock = threading.Lock()
        self.hosts = {}             # key → {"status", "note", "retry_at", "backoff", "tunnel"}
        self.targets = {}           # key → ssh arguments, as last seen from a gateway
        self.dismissed = set()      # "Not now", until the bridge restarts

    def status(self, key, target, name):
        """{"state", "note", "enabled"} of a remote pane's host; an enabled one starts
        connecting when due. States: ask, dismissed, enabling, connecting, up, down."""
        self.targets[key] = list(target)
        e = agentctl.entry(key)
        with self.lock:
            h = self.hosts.setdefault(key, {"status": "new", "note": "", "retry_at": 0.0, "backoff": 1.0})
            if not e:
                if h["status"] == "enabling":
                    return {"state": "enabling", "note": f"setting up {name}…", "enabled": False}
                state = "dismissed" if key in self.dismissed else "ask"
                return {"state": state, "note": h["note"] or f"remote host {name}", "enabled": False}
            if h["status"] == "up":
                return {"state": "up", "note": f"{name} (helper)", "enabled": True}
            if h["status"] not in ("connecting", "enabling") and time.monotonic() >= h["retry_at"]:
                h.update(status="connecting", note=f"connecting to {name}…")
                threading.Thread(target=self._run, args=(key, e["ssh"]), name=f"agent-{key}", daemon=True).start()
            return {"state": h["status"] if h["status"] in ("connecting", "down") else "connecting", "note": h["note"], "enabled": True}

    # ── the panel's buttons ──────────────────────────────────────────────────

    def enable(self, key, by=None):
        """Enable: copy the agent over the gateway's ssh, then connect (in a thread)."""
        target = self.targets.get(key) or (agentctl.entry(key) or {}).get("ssh")
        if not target:
            return self._tell(by, f"{key}: no ssh connection known for this host")
        self.dismissed.discard(key)
        with self.lock:
            h = self.hosts.setdefault(key, {"status": "new", "note": "", "retry_at": 0.0, "backoff": 1.0})
            if h["status"] == "enabling":
                return  # one at a time: a second window's click joins the first
            h.update(status="enabling", note="")

        def run():
            try:
                hosts.enable(target)
                log(f"agent: enabled {key}")
                self._set(key, status="new", note="", retry_at=0.0, backoff=1.0)
            except (agentctl.AgentError, OSError) as e:
                self._tell(by, f"Could not enable {key}: {e}")  # the toast first, then the button returns
                self._set(key, status="new", note=str(e))
        threading.Thread(target=run, name=f"enable-{key}", daemon=True).start()

    def dismiss(self, key):
        self.dismissed.add(key)

    def remove(self, key, by=None):
        """Stop the connection, take the helper off the host, forget it (in a thread)."""
        target = (agentctl.entry(key) or {}).get("ssh") or self.targets.get(key)
        agentctl.forget(key)            # at once: no poll may reconnect it meanwhile
        with self.lock:
            t = self.hosts.pop(key, {}).get("tunnel")
        if t:
            t.stop()
            try:
                self.post("/internal/remote", {"host": key, "socket": None, "token": None})
            except OSError:
                pass
        self.dismissed.add(key)

        def run():
            try:
                if target:
                    agentctl.remove(target)
                log(f"agent: removed from {key}")
            except (agentctl.AgentError, OSError) as e:
                self._tell(by, f"Could not remove the helper from {key}: {e}")
        threading.Thread(target=run, name=f"remove-{key}", daemon=True).start()

    def _tell(self, by, message):
        log(f"agent: {message}")
        try:
            self.post("/internal/error", {"message": message, "by": by})
        except OSError:
            pass

    # ── connections ──────────────────────────────────────────────────────────

    def _set(self, key, **kw):
        with self.lock:
            self.hosts.setdefault(key, {"status": "new", "note": "", "retry_at": 0.0, "backoff": 1.0}).update(kw)

    def _connect(self, key, target):
        t = agentctl.Tunnel(target, agentctl.socket_for(key))
        agent_id, _, _, platform = t.start()
        if hosts.LOCAL_AGENT_ID and agent_id != hosts.LOCAL_AGENT_ID:  # bring it up to the running build
            t.stop()
            binary = BUILD_DIR / "agents" / platform / "fbd"
            if not binary.is_file():
                raise agentctl.AgentError(f"helper outdated · iterm-filebrowser hosts enable {key}")
            log(f"agent on {key}: {agent_id} → {hosts.LOCAL_AGENT_ID}")
            agentctl.install(target, binary, hosts.LOCAL_AGENT_ID)
            record = agentctl.load()
            record.setdefault(key, {"ssh": list(target)})["agent_id"] = hosts.LOCAL_AGENT_ID
            agentctl.save(record)
            t = agentctl.Tunnel(target, agentctl.socket_for(key))
            t.start()
        return t

    def _run(self, key, target):
        try:
            t = self._connect(key, target)
        except (agentctl.AgentError, OSError) as e:
            return self._failed(key, str(e))
        with self.lock:  # removed while connecting: this connection must not come back
            gone = key not in self.hosts or agentctl.entry(key) is None
        if gone:
            return t.stop()
        try:
            self.post("/internal/remote", {"host": key, "socket": str(t.local), "token": t.token})
        except OSError as e:  # fbd restarting: drop this connection, the next try registers
            t.stop()
            return self._failed(key, f"fbd did not take {key}: {e}")
        self._set(key, status="up", note="", backoff=1.0, tunnel=t)
        log(f"agent on {key} connected")
        t.proc.wait()                                   # until the connection ends
        try:
            self.post("/internal/remote", {"host": key, "socket": None, "token": None})
        except OSError:
            pass
        t.stop()
        if key in self.hosts:                           # not removed meanwhile
            self._failed(key, f"{key}: connection closed")

    def _failed(self, key, why):
        log(f"agent: {why}")
        with self.lock:
            h = self.hosts.setdefault(key, {"status": "new", "note": "", "retry_at": 0.0, "backoff": 1.0})
            h.update(status="down", note=why, retry_at=time.monotonic() + h["backoff"], backoff=min(h["backoff"] * 2, BACKOFF_MAX))

    def reregister(self):
        """A restarted fbd knows no hosts: tell it the connected ones again."""
        with self.lock:
            live = [(k, d["tunnel"]) for k, d in self.hosts.items() if d["status"] == "up" and d.get("tunnel")]
        for key, t in live:
            try:
                self.post("/internal/remote", {"host": key, "socket": str(t.local), "token": t.token})
            except OSError as e:
                log(f"agent: fbd did not take {key}: {e}")

    def stop(self):
        with self.lock:
            tunnels = [h.get("tunnel") for h in self.hosts.values()]
        for t in tunnels:
            if t:
                t.stop()
