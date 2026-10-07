# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The bridge's side of remote hosts (AC-37, AC-38). A focused tmux -CC pane names its host
by the ssh arguments of its gateway (`key`). An enabled host (agents.json) gets one ssh
connection carrying its agent, registered with fbd, its agent brought up to the running
build (AC-42: for every open window of the host, not only the focused one; the panel says
so), reconnected with back-off. Any other host is offered once per bridge run: Enable
copies the agent over that ssh (hosts.enable), "Not now" hides the offer until the bridge
restarts. Threads, so ssh never blocks the poll loop."""
import os
import threading
import time
import secrets

from . import agentctl, hosts
from .common import BUILD, log

BACKOFF_MAX = 30.0
UPDATE_RETRY = 600.0  # s before a failed helper update is tried again (or the next bridge start)
UPDATED_NOTE = 60.0  # s the panel keeps saying a host's helper was updated


class UpdateError(agentctl.AgentError):
    """The helper on a host could not be brought up to the running build."""


class Remotes:
    def __init__(self, post):
        self.post = post            # backend.post: (path, body)
        self.lock = threading.Lock()
        self.hosts = {}             # key → {"status", "note", "retry_at", "backoff", "tunnel"}
        self.targets = {}           # key → ssh arguments, as last seen from a gateway
        self.dismissed = set()      # "Not now", until the bridge restarts
        self.lifecycles = {}        # per-host publication and teardown, never a global I/O lock

    def _lifecycle(self, key):
        with self.lock:
            return self.lifecycles.setdefault(key, threading.RLock())

    def _owns(self, key, owner):
        with self.lock:
            return self.hosts.get(key) is owner

    def place(self, r):
        """Where a resolved pane's files are: {"cwd", "host", "remote", "note"}. A host's pane has
        `remote` (its offer, for the panel) and, once its helper is enabled, `host` and the host's
        own path, never resolved on this Mac (AC-37, AC-38); a local pane has its real folder."""
        if not r.get("remote_key"):
            return {"cwd": os.path.realpath(r["cwd"]) if r.get("cwd") else None, "host": None, "remote": None, "note": None}
        st = self.status(r["remote_key"], r["ssh"], r["remote_host"])
        remote = {"key": r["remote_key"], "name": r["remote_host"], "state": st["state"],
                  **({"updated": st["updated"]} if st.get("updated") else {})}
        enabled = st["enabled"]
        return {"cwd": r.get("path") if enabled else None, "host": r["remote_key"] if enabled else None,
                "remote": remote, "note": st["note"]}

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
                if time.monotonic() - h.get("updated_at", -UPDATED_NOTE) < UPDATED_NOTE:
                    return {"state": "up", "note": f"{name} (helper updated to {BUILD})", "enabled": True, "updated": BUILD}
                return {"state": "up", "note": f"{name} (helper)", "enabled": True}
            if h["status"] not in ("connecting", "enabling", "updating") and time.monotonic() >= h["retry_at"]:
                h.update(status="connecting", note=f"connecting to {name}…")
                threading.Thread(target=self._run, args=(key, e["ssh"], h), name=f"agent-{key}", daemon=True).start()
            return {"state": h["status"] if h["status"] in ("connecting", "updating", "down") else "connecting", "note": h["note"], "enabled": True}

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
                with self._lifecycle(key):
                    if not self._owns(key, h):
                        return
                    hosts.enable(target, active=lambda: self._owns(key, h))
                if not self._owns(key, h):
                    return
                log(f"agent: enabled {key}")
                self._set(key, owner=h, status="new", note="", retry_at=0.0, backoff=1.0)
            except (agentctl.AgentError, OSError) as e:
                self._tell(by, f"Could not enable {key}: {e}")  # the toast first, then the button returns
                self._set(key, owner=h, status="new", note=str(e))
        threading.Thread(target=run, name=f"enable-{key}", daemon=True).start()

    def dismiss(self, key):
        self.dismissed.add(key)

    def remove(self, key, by=None):
        """Stop the connection, take the helper off the host, forget it (in a thread)."""
        target = (agentctl.entry(key) or {}).get("ssh") or self.targets.get(key)
        lifecycle = self._lifecycle(key)
        old = {}

        def forget(record):
            nonlocal old
            with self.lock:
                old = self.hosts.pop(key, {})
                self.dismissed.add(key)
            record.pop(key, None)
        agentctl.update(forget)         # invalidation and forgetting are one transaction

        def run():
            try:
                with lifecycle:
                    t = old.get("tunnel")
                    if t:
                        t.stop()
                    with self.lock:
                        replacement = self.hosts.get(key)
                        replaced = replacement is not None and replacement.get("status") != "new"
                    if not replaced:
                        self._unregister(key)
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

    def _unregister(self, key):
        try:
            self.post("/internal/remote", {"host": key, "socket": None, "token": None})
        except OSError:
            pass

    # ── connections ──────────────────────────────────────────────────────────

    def _set(self, key, owner=None, **kw):
        with self.lock:
            h = self.hosts.get(key)
            if owner is not None and h is not owner:
                return
            if h is None:
                h = self.hosts.setdefault(key, {"status": "new", "note": "", "retry_at": 0.0, "backoff": 1.0})
            h.update(kw)

    def _connect(self, key, target, owner=None):
        socket = agentctl.socket_for(f"{key} {secrets.token_hex(6)}")
        t = agentctl.Tunnel(target, socket, hosts.LOCAL_AGENT_ID)
        agent_id, _, _, platform = t.start()
        if hosts.LOCAL_AGENT_ID and agent_id != hosts.LOCAL_AGENT_ID:  # bring it up to the running build
            t.stop()
            try:
                binary = hosts.binary_for(platform)
            except agentctl.AgentError as e:
                raise agentctl.AgentError(f"helper outdated · iterm-enhancer hosts enable {key}: {e}") from e
            with self._lifecycle(key):
                if owner is not None and not self._owns(key, owner):
                    return t
                name = (agentctl.entry(key) or {}).get("name", key)
                self._set(key, owner=owner, status="updating", note=f"updating the helper on {name}…")
                log(f"agent on {key}: {agent_id} → {hosts.LOCAL_AGENT_ID}")
                try:
                    agentctl.install(target, binary, hosts.LOCAL_AGENT_ID)
                except agentctl.AgentError as e:
                    raise UpdateError(f"Could not update the helper on {name}: {e}") from e

                def update_id(record):
                    if key in record and (owner is None or self._owns(key, owner)):
                        record[key]["agent_id"] = hosts.LOCAL_AGENT_ID
                agentctl.update(update_id)
                t = agentctl.Tunnel(target, socket, hosts.LOCAL_AGENT_ID)
                t.start()
                self._set(key, owner=owner, updated_at=time.monotonic())
        return t

    def _run(self, key, target, owner):
        try:
            t = self._connect(key, target, owner)
        except UpdateError as e:  # copying 6 MB again every 30 s would not help
            return self._failed(key, str(e), wait=UPDATE_RETRY, owner=owner)
        except (agentctl.AgentError, OSError) as e:
            return self._failed(key, str(e), owner=owner)
        with self._lifecycle(key):
            if not self._owns(key, owner) or agentctl.entry(key) is None:
                return t.stop()
            try:
                self.post("/internal/remote", {"host": key, "socket": str(t.local), "token": t.token})
            except OSError as e:  # fbd restarting: drop this connection, the next try registers
                t.stop()
                return self._failed(key, f"fbd did not take {key}: {e}", owner=owner)
            if not self._owns(key, owner) or agentctl.entry(key) is None:
                self._unregister(key)
                return t.stop()
            self._set(key, owner=owner, status="up", note="", backoff=1.0, tunnel=t)
        log(f"agent on {key} connected")
        t.proc.wait()                                   # until the connection ends
        with self._lifecycle(key):
            if self._owns(key, owner):
                self._unregister(key)
                self._failed(key, f"{key}: connection closed", owner=owner)
            t.stop()

    def _failed(self, key, why, wait=0.0, owner=None):
        log(f"agent: {why}")
        with self.lock:
            h = self.hosts.get(key)
            if owner is not None and h is not owner:
                return
            if h is None:
                h = self.hosts.setdefault(key, {"status": "new", "note": "", "retry_at": 0.0, "backoff": 1.0})
            h.update(status="down", note=why, retry_at=time.monotonic() + max(h["backoff"], wait), backoff=min(h["backoff"] * 2, BACKOFF_MAX))

    def reregister(self):
        """A restarted fbd knows no hosts: tell it the connected ones again."""
        with self.lock:
            live = [(k, d["tunnel"]) for k, d in self.hosts.items() if d["status"] == "up" and d.get("tunnel")]
        for key, t in live:
            with self._lifecycle(key):
                with self.lock:
                    current = self.hosts.get(key, {}).get("tunnel") is t
                if current and agentctl.entry(key) is not None:
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
