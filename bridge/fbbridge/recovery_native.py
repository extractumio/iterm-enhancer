# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Ordered native split reconstruction with creation-time retry identities."""
import asyncio
import os
import pwd
import shlex

import iterm2

from .common import SHELLS, UserError
from .recovery_capture import canonical, layout
from .recovery_liveness import session_state
from .recovery_recipes import directory_available, fallback_directory, launch_profile, local_shell, ssh_command
from .resolve import resolve


def leaves(node):
    return [node["pane"]] if "pane" in node else [p for child in node["children"] for p in leaves(child)]


def translate(node, mapping):
    if "pane" in node:
        return {"pane": mapping.get(node["pane"], "unrelated:" + node["pane"])}
    return {"vertical": node["vertical"], "children": [translate(c, mapping) for c in node["children"]]}


def shell_command():
    shell = pwd.getpwuid(os.getuid()).pw_shell
    return shlex.join([shell if os.path.basename(shell) in SHELLS else "/bin/sh", "-l"])


class NativeRestore:
    def __init__(self, runner, tmux):
        self.runner, self.tmux = runner, tmux
        self.app, self.conn = runner.app, runner.conn
        self.profiles = {}
        self.mapping = {}
        self.uncertain = set()

    def marker(self, sid):
        return f"iterm-enhancer Restore {self.runner.job['id']}:{sid}"

    async def discover(self, profiles=True):
        await self.app.async_refresh()
        self.mapping = {}
        self.uncertain = set()
        source = {p["id"] for w in self.runner.snapshot["windows"] for t in w["tabs"] for p in t["panes"]}
        markers = {self.marker(sid): sid for sid in source}
        journal = {step["session"]: old for old, step in self.runner.job["steps"].items()
                   if old in source and step.get("session")}
        def bind(old, current):
            if old in self.mapping and self.mapping[old] != current:
                raise UserError("Duplicate recovery identities; existing terminals preserved")
            self.mapping[old] = current
        for w in self.app.terminal_windows:
            for t in w.tabs:
                for s in t.all_sessions:
                    value = await s.async_get_variable("profileName")
                    old = markers.get(value) or journal.get(s.session_id)
                    identities = {sid for sid in (s.session_id if s.session_id in source else None, old) if sid}
                    if not identities:
                        continue
                    state = await session_state(s)
                    if state != "ended":
                        if state == "unknown": self.uncertain.add(s.session_id)
                        for identity in identities:
                            bind(identity, s.session_id)
                    else:
                        # Restart would rerun the previous program, including on a
                        # second reboot with a creation marker or journal identity.
                        await self.runner.step("ended:" + s.session_id, "deviation", "Ended native pane preserved; replacement uses a new controlled shell", s)
        if profiles:
            rows = await iterm2.PartialProfile.async_query(self.conn, properties=["Guid", "Name"])
            names = [p.name for p in rows]
            self.profiles = {p.guid: p.name for p in rows if names.count(p.name) == 1}

    def session(self, sid):
        target = self.mapping.get(sid)
        return self.app.get_session_by_id(target) if target else None

    async def guard_eligible(self, panes):
        observer = getattr(self.runner.capture, "lifecycle", None)
        if observer:
            await asyncio.wait_for(observer.poll(), 4)
            if any(observer.blocked(pane["id"], self.runner.job["steps"].get(pane["id"], {}).get("session")) for pane in panes):
                raise UserError("Intentionally terminated session; recovery skipped")
        status = await self.runner.capture.call("GET", "/internal/recovery")
        if any(pane["id"] in status.get("retired", []) for pane in panes):
            raise UserError("Intentionally terminated session; recovery skipped")

    async def guard_creation(self, pane):
        await self.guard_eligible([pane])
        # Recheck immediately before creation; public creation APIs cannot run in
        # an iTerm2 transaction (owned tests deadlock). Preserve a late live ID.
        ids = {pane["id"], self.runner.job["steps"].get(pane["id"], {}).get("session")}
        response = await iterm2.rpc.async_list_sessions(self.conn)
        for proto in response.list_sessions_response.windows:
            window = iterm2.Window.create_from_proto(self.conn, proto)
            if window is None:
                raise UserError("Session inventory incomplete; retry available")
            for tab in window.tabs:
                for s in tab.all_sessions:
                    if s.session_id in ids or await s.async_get_variable("profileName") == self.marker(pane["id"]):
                        if await session_state(s) != "ended":
                            raise UserError("Native session reopened during recovery; Retry will adopt it")

    async def recipe(self, pane):
        kind, cwd = pane["connection"]["kind"], pane.get("cwd")
        messages = []
        if pane["cwd_status"] != "known":
            messages.append("Directory is stale or unknown")
        if pane.get("profile") and pane["profile"] not in self.profiles:
            messages.append("Saved profile unavailable; default appearance used")
        if kind == "ssh":
            if pane["cwd_status"] != "known":
                cwd = None
            command, local = ssh_command(pane["connection"]["args"], cwd), None
            messages.append("SSH started; authentication and remote directory must be checked in the pane")
        elif kind == "tmux":
            command, local = await self.tmux.command(pane["connection"], client_id=pane["id"]), None
        else:
            command, local = shell_command(), cwd
            if kind == "unsupported":
                messages.append(pane["connection"]["reason"] + "; shell opened")
                local = None  # never interpret an unverified remote path as local
            elif not directory_available(cwd):
                local = None
                messages.append("Recorded local directory unavailable; shell opened in home or filesystem root")
            command, local = local_shell(command, local), None
        if pane.get("job") and pane["job"] not in SHELLS and kind not in ("tmux", "ssh"):
            messages.append("Previous application was not restarted")
        return command, local, "; ".join(messages)

    async def properties(self, pane):
        await self.guard_eligible([pane])
        command, cwd, message = await self.recipe(pane)
        observer = getattr(self.runner.capture, "lifecycle", None)
        if observer:
            observer.remember(self.marker(pane["id"]), pane)
        return launch_profile(self.marker(pane["id"]), command, cwd,
                              close_on_end=pane["connection"]["kind"] not in ("ssh", "tmux")), message

    async def created(self, pane, s, message):
        self.mapping[pane["id"]] = s.session_id
        await self.app.async_refresh()
        s = self.app.get_session_by_id(s.session_id)
        await self.runner.step(pane["id"], "deviation" if message else "restored", message or "Terminal created", s)
        return s

    async def inspect(self, pane, s):
        # An exact ID or creation marker is mandatory; cwd/name alone is no identity.
        if s.session_id in self.uncertain:
            await self.runner.step(pane["id"], "deviation", "Terminal liveness unavailable; identified pane preserved", s)
            return False
        r = await resolve(self.conn, s)
        owned = await s.async_get_variable("profileName") == self.marker(pane["id"])
        expected = pane.get("cwd")
        if owned and not directory_available(expected):
            expected = fallback_directory()
        matching = r.get("cwd") == expected and pane["connection"]["kind"] == "shell"
        if pane["connection"]["kind"] == "unsupported":
            matching = owned and r.get("cwd") == fallback_directory()
        if pane["connection"]["kind"] == "tmux":
            c = pane["connection"]
            matching = r.get("key") == c["server"] + ":" + c["pane"]
        if pane["connection"]["kind"] == "ssh":
            matching = r.get("mode") == "remote" and (not pane.get("cwd") or await s.async_get_variable("path") == pane["cwd"])
        prior = self.runner.job["steps"].get(pane["id"], {})
        message = "Identified existing terminal preserved"
        state = "adopted" if matching and not r.get("busy") else "deviation"
        if owned and not r.get("busy") and (matching or pane["connection"]["kind"] != "shell"):
            # A retry may see an SSH password prompt or a still-starting shell.
            # Its creation identity is enough to prevent a duplicate, never to restart it.
            state, message = prior.get("state", "adopted"), prior.get("message", message)
        elif r.get("busy"):
            message = "Running session preserved; previous application not restarted"
        elif not matching:
            message = "Directory or connection changed; live session preserved"
        await self.runner.step(pane["id"], state, message, s)
        return owned and matching and not r.get("busy")

    async def check(self, tab, saved):
        mapping = {new: old for old, new in self.mapping.items()}
        actual = canonical(translate(layout(tab.root), mapping))
        present = {p for p in leaves(saved["tree"]) if self.session(p)}
        if actual != canonical(saved["tree"], present):
            raise UserError("Terminal split layout changed; existing panes preserved")

    async def split_tree(self, node, panes):
        if "pane" in node:
            return
        anchors = [leaves(c)[0] for c in node["children"]]
        anchor = self.session(anchors[0])
        for sid in anchors[1:]:
            await self.discover(profiles=False)
            if self.session(sid):
                anchor = self.session(sid)
                continue
            pane = panes[sid]
            properties, message = await self.properties(pane)
            await self.runner.step(sid, "pending", "Creating split")
            await self.guard_creation(pane)
            new = await anchor.async_split_pane(vertical=node["vertical"], before=False,
                                               profile=self.profiles.get(pane.get("profile")), profile_customizations=properties)
            anchor = await self.created(pane, new, message)
        for child in node["children"]:
            await self.split_tree(child, panes)

    async def tab(self, saved, window):
        await self.guard_eligible(saved["panes"])
        if saved["control"]:
            return await self.control(saved)
        panes = {p["id"]: p for p in saved["panes"]}
        existing = [self.session(p) for p in panes if self.session(p)]
        tab = existing[0].tab if existing else None
        if tab:
            if any(s.tab.tab_id != tab.tab_id for s in existing):
                raise UserError("Identified panes now belong to different tabs; preserved")
            await self.check(tab, saved)
            # Only a tab wholly owned by this job can grow on retry. A partially
            # reopened native tab is preserved rather than guessing its missing splits.
            if len(existing) != len(panes) and any([await self.session(p).async_get_variable("profileName") != self.marker(p)
                                                   for p in panes if self.session(p)]):
                raise UserError("Partially reopened native tab; missing panes require manual recovery")
            mutable = []
            for p in panes:
                if self.session(p):
                    mutable.append(await self.inspect(panes[p], self.session(p)))
        else:
            first = panes[leaves(saved["tree"])[0]]
            properties, message = await self.properties(first)
            await self.runner.step(first["id"], "pending", "Creating tab")
            await self.discover(profiles=False)
            if self.session(first["id"]):
                return await self.tab(saved, window)
            await self.guard_creation(first)
            if window:
                tab = await window.async_create_tab(profile=self.profiles.get(first.get("profile")), profile_customizations=properties)
            else:
                window = await iterm2.Window.async_create(self.conn, profile=self.profiles.get(first.get("profile")), profile_customizations=properties)
                tab = window.current_tab
            await self.created(first, tab.current_session, message)
            mutable = [True]
        if existing and len(existing) != len(panes) and not all(mutable):
            raise UserError("Partially restored tab is busy or changed; existing panes preserved")
        await self.split_tree(saved["tree"], panes)
        await self.app.async_refresh()
        tab = self.session(leaves(saved["tree"])[0]).tab
        await self.check(tab, saved)
        owned = all(mutable) and all([await self.session(old).async_get_variable("profileName") == self.marker(old) for old in panes if self.session(old)])
        if owned:
            for old, p in panes.items():
                self.session(old).preferred_size = iterm2.util.Size(int(p["grid"]["width"]), int(p["grid"]["height"]))
            await tab.async_update_layout()
            if saved["active"] and self.session(saved["active"]):
                await self.session(saved["active"]).async_activate()
        return tab, owned

    async def control(self, saved):
        await self.guard_eligible(saved["panes"])
        existing = [self.session(p["id"]) for p in saved["panes"] if self.session(p["id"])]
        if existing:
            if len(existing) != len(saved["panes"]):
                raise UserError("Partially reopened tmux control tab; existing panes preserved")
            if any(s.tab.tab_id != existing[0].tab.tab_id for s in existing):
                raise UserError("tmux control panes now belong to different tabs; preserved")
            await self.check(existing[0].tab, saved)
            for p in saved["panes"]:
                if self.session(p["id"]):
                    await self.runner.step(p["id"], "adopted", "Existing tmux control pane preserved", self.session(p["id"]))
            return existing[0].tab, False
        # One gateway per tmux session. Its creation marker survives a journal gap.
        first = saved["panes"][0]
        recipe = first["connection"]
        if recipe["kind"] != "tmux":
            raise UserError("tmux control graph is unavailable")
        result = await self.tmux.connection(recipe)
        gateway_key = "gateway:" + recipe["server"] + ":" + recipe["session"]
        marker = self.marker(gateway_key)
        sessions = [s for w in self.app.terminal_windows for t in w.tabs for s in t.all_sessions] + list(self.app.buried_sessions)
        gateways = [s for s in sessions
                    if await s.async_get_variable("profileName") == marker]
        gateway = gateways[0] if gateways else None
        if gateway is None:
            properties = launch_profile(marker, await self.tmux.command(recipe), close_on_end=False)
            await self.runner.step(gateway_key, "pending", "Opening tmux control connection")
            await self.guard_eligible(saved["panes"])
            window = await iterm2.Window.async_create(self.conn, profile_customizations=properties)
            gateway = window.current_tab.current_session
            await self.runner.step(gateway_key, "restored", "tmux control gateway created", gateway)
        for _ in range(60):
            await self.app.async_refresh()
            connections = await iterm2.async_get_tmux_connections(self.conn)
            tc = next((c for c in connections if c.owning_session and c.owning_session.session_id == gateway.session_id), None)
            if tc:
                for w in self.app.terminal_windows:
                    for t in w.tabs:
                        if t.tmux_connection_id != tc.connection_id:
                            continue
                        mapping = {str(await s.async_get_variable("tmuxWindowPane")).lstrip("%"): s for s in t.all_sessions}
                        expected = {result["panes"].get(p["connection"]["pane"], "").lstrip("%") for p in saved["panes"]}
                        if expected == set(mapping):
                            for p in saved["panes"]:
                                s = mapping[result["panes"][p["connection"]["pane"]].lstrip("%")]
                                await self.created(p, s, "" if result["live"] else "tmux recreated with shells; previous applications not restarted")
                            return t, not result["live"]
            await asyncio.sleep(0.25)
        raise UserError("tmux control tab did not appear; gateway preserved for retry")
