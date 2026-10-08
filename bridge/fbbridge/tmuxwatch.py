# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""A tmux -CC integration iTerm2 dropped (AC-54). iTerm2 can leave tmux mode while tmux keeps
its control client attached; the raw protocol then prints into the gateway session (and the
web app's mirror of it). The bridge detaches that client once, so tmux and its windows keep
running, tells the user, and can attach again from what it recorded while the connection
was alive.

Writing "detach-client" without a click is the owner's decision (AC-54). It is written only
to a session the bridge itself saw owning a tmux connection that has since gone and that owns
none now, whose newest line on screen is tmux protocol, while protocol lines keep arriving
(at least two changes, the last within two looks) for GRACE seconds, and for a local gateway
only while a tmux process runs under it: never to a shell that shows old protocol lines, a
prompt under them, or typing at it."""
import asyncio
import hashlib
import re
import secrets
import shlex
import time

import iterm2

from .common import UserError, log
from .procinfo import find_descendant
from .recovery_recipes import launch_profile, ssh_command
from .recovery_tmux import binary
from .resolve import gateway_target
from .sshargs import key as ssh_key

EVERY = 2.0           # seconds between looks
GRACE = 5.0           # seconds of fresh protocol output before the client is detached
FRESH = 2 * EVERY + 0.5   # the last change must be this recent
QUIET_LOOKS = 5       # looks without protocol after which a gone connection's gateway is forgotten
ROWS = 40             # screen rows looked at
PROTOCOL = re.compile(r"^%(output|extended-output|begin|end|error|layout-change|window-\w+|unlinked-window-\w+"
                      r"|session-\w+|sessions-changed|client-\w+|pane-mode-changed|pause|continue|subscription-changed)\b")
INFO = "#{socket_path}\t#{session_name}"


def logical_lines(rows):
    """Screen rows ((text, hard end)) joined where the terminal wrapped them: a long protocol
    line spans several rows."""
    lines, cur = [], ""
    for text, hard in rows:
        cur += text
        if hard:
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def protocol_screen(rows):
    """A hash of the protocol lines when the newest line is tmux protocol and there are at least
    two, else None: a prompt or typing below old protocol lines is not a stream."""
    lines = [l for l in logical_lines(rows) if l.strip()]
    proto = [l for l in lines if PROTOCOL.match(l)]
    if not lines or not PROTOCOL.match(lines[-1]) or len(proto) < 2:
        return None
    return hashlib.sha1("\n".join(proto).encode()).hexdigest()


def attach_command(drop):
    """The command that attaches to the recorded tmux session again, quoted by the recovery rules."""
    if not drop.get("socket") or not drop.get("session"):
        raise UserError(f"The tmux session on {drop['host']} was not recorded; attach to it by hand.")
    tmux = ["tmux" if drop["target"] else binary(), "-S", drop["socket"], "-CC", "attach-session", "-t", drop["session"]]
    return ssh_command(drop["target"], command=shlex.join(tmux)) if drop["target"] else shlex.join(tmux)


class TmuxWatch:
    def __init__(self, conn, app):
        self.conn, self.app = conn, app
        self.gateways = {}     # connection id -> what the bridge saw while it was alive
        self.missing = {}      # gateway session id -> {"since", "hash", "changes", "last"}
        self.quiet = {}        # gateway session id -> looks without protocol since its connection went
        self.dropped = {}      # drop id -> the gateway's record, "sid", "window_id", "at"
        self.listeners = []    # called with no arguments when `dropped` changes (the web app)
        self.alerting = None   # the one alert task

    async def follow(self):
        while True:
            try:
                await self.tick(time.monotonic())
            except Exception as e:  # watching must never stop the bridge
                log(f"tmux watch: {type(e).__name__}: {e}")
            await asyncio.sleep(EVERY)

    async def tick(self, now):
        live = {c.connection_id: c for c in await iterm2.async_get_tmux_connections(self.conn)}
        for cid, c in live.items():
            if cid not in self.gateways and c.owning_session:
                self.gateways[cid] = await self.describe(c)
        owners = {c.owning_session.session_id for c in live.values() if c.owning_session}
        for cid in [k for k in self.gateways if k not in live]:
            g = self.gateways[cid]
            session = self.app.get_session_by_id(g["sid"])
            if not session or g["sid"] in owners or self.quiet.get(g["sid"], 0) >= QUIET_LOOKS:
                self.forget(cid)                 # closed, attached again, or ended normally
            elif await self.streaming(session, g, now):
                self.forget(cid)
                await self.detach(session, g)

    def forget(self, cid):
        g = self.gateways.pop(cid)
        self.missing.pop(g["sid"], None)
        self.quiet.pop(g["sid"], None)

    async def describe(self, c):
        s = c.owning_session
        target = await gateway_target(c)
        g = {"sid": s.session_id, "pid": await s.async_get_variable("pid"), "target": target,
             "host": ssh_key(target) if target else "this Mac", "socket": None, "session": None}
        try:
            g["socket"], g["session"] = (await c.async_send_command(f"display -p '{INFO}'")).strip().split("\t", 1)
        except Exception as e:  # recorded without them: Reattach then says so
            log(f"tmux watch: display -p '{INFO}' on {g['host']} failed: {type(e).__name__}: {e}")
        return g

    async def streaming(self, session, g, now):
        """True once protocol output has kept arriving for GRACE seconds after the connection went."""
        screen = await session.async_get_screen_contents()
        rows = [(screen.line(i).string, getattr(screen.line(i), "hard_eol", True))
                for i in range(max(0, screen.number_of_lines - ROWS), screen.number_of_lines)]
        h = protocol_screen(rows)
        m = self.missing.get(g["sid"])
        if h is None:
            self.missing.pop(g["sid"], None)
            self.quiet[g["sid"]] = self.quiet.get(g["sid"], 0) + 1
            return False
        self.quiet.pop(g["sid"], None)
        if m is None:
            self.missing[g["sid"]] = {"since": now, "hash": h, "changes": 0, "last": None}
            return False
        if h != m["hash"]:
            m.update(hash=h, changes=m["changes"] + 1, last=now)
        if m["changes"] < 2 or now - m["last"] > FRESH or now - m["since"] < GRACE:
            return False
        return bool(g["target"]) or bool(g["pid"] and find_descendant(g["pid"], "tmux"))

    def suspect(self, sid):
        """The session is a gateway whose raw protocol is showing: not mirrored, not typed into."""
        return any(d["sid"] == sid for d in self.dropped.values()) or sid in self.missing

    async def detach(self, session, g):
        log(f"tmux watch: integration with {g['host']} dropped (gateway {g['sid']}, tmux session {g['session']}): detaching the client")
        await session.async_send_text("detach-client\n", suppress_broadcast=True)   # a tmux command on control-mode input
        drop_id = secrets.token_hex(6)
        self.dropped[drop_id] = {**g, "window_id": session.window.window_id if session.window else None, "at": time.time()}
        self.changed()
        if not self.alerting or self.alerting.done():
            self.alerting = asyncio.create_task(self.alert())

    def changed(self):
        for f in list(self.listeners):
            f()

    def items(self):
        return [{"id": k, "host": d["host"], "session": d["session"]} for k, d in self.dropped.items()]

    async def alert(self):
        """One iTerm2 alert at a time, for every drop not yet answered (on the web too)."""
        shown = set()
        while pending := [k for k in self.dropped if k not in shown]:
            shown.update(pending)
            ds = [self.dropped[k] for k in pending]
            hosts = ", ".join(sorted({d["host"] for d in ds}))
            names = ", ".join(sorted({d["session"] or "?" for d in ds}))
            window = next((d["window_id"] for d in ds if d["window_id"] and self.app.get_window_by_id(d["window_id"])), None)
            a = iterm2.Alert(f"tmux integration with {hosts} dropped",
                             f"iterm-enhancer detached it from the raw tmux output. The tmux session {names} keeps "
                             f"running on {hosts}; Reattach opens it in iTerm2 again.", window_id=window)
            a.add_button("Reattach")
            a.add_button("Later")
            try:
                choice = await a.async_run(self.conn)
            except Exception as e:  # the window went away under the sheet: ask without it next time
                log(f"tmux watch: alert: {type(e).__name__}: {e}")
                return
            if choice == 1000:
                for k in pending:
                    if k in self.dropped:
                        try:
                            await self.reattach(k)
                        except Exception as e:  # fail loud: the user asked and waits for it
                            log(f"tmux watch: reattach: {type(e).__name__}: {e}")
                            await self.say(f"Reattach failed: {e}")

    async def say(self, text):
        try:
            await iterm2.Alert("tmux reattach", text).async_run(self.conn)
        except Exception as e:  # nothing more can be shown
            log(f"tmux watch: alert: {type(e).__name__}: {e}")

    async def reattach(self, drop_id):
        d = self.dropped.get(drop_id)
        if not d:
            raise UserError("This tmux session was reattached or dismissed already.")
        command = attach_command(d)
        await iterm2.Window.async_create(self.conn, profile_customizations=launch_profile(f"tmux {d['host']}", command, close_on_end=False))
        log(f"tmux watch: reattaching {d['session']} on {d['host']}")
        del self.dropped[drop_id]
        self.changed()

    def dismiss(self, drop_id):
        if self.dropped.pop(drop_id, None):
            self.changed()
