# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Verified live attachment or shell-only reconstruction on a private tmux socket."""
import os
import hashlib
import json
import re
import shlex
import shutil
import socket as sockets
import stat
from pathlib import Path

from . import procinfo
from .common import SHELLS, UserError
from .recovery_tmux_capture import local_command
from .recovery_recipes import directory_available, directory_bootstrap, fallback_directory, ssh_command


def binary():
    return shutil.which("tmux") or next((p for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux") if os.path.isfile(p)), "tmux")


def private_absent(path):
    """A stale owned socket with no listener is distinct from an unknown server."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        return False
    with sockets.socket(sockets.AF_UNIX, sockets.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        try:
            probe.connect(path)
        except (FileNotFoundError, ConnectionRefusedError):
            return True
        except OSError:
            return False
    return False


def remap_layout(value, mapping):
    """Replace only leaf pane IDs, then recompute tmux's rotating checksum."""
    body = value.split(",", 1)[1]
    def leaf(m):
        old = "%" + m.group(2)
        if old not in mapping:
            raise UserError("Incomplete tmux pane map")
        return m.group(1) + mapping[old].lstrip("%")
    body = re.sub(r"(\d+x\d+,\d+,\d+,)(\d+)(?=[,}\]]|$)", leaf, body)
    checksum = 0
    for c in body.encode("ascii"):
        checksum = ((checksum >> 1) | ((checksum & 1) << 15)) + c
        checksum &= 0xffff
    return f"{checksum:04x},{body}"


class TmuxRestore:
    def __init__(self, runner, snapshot):
        self.runner, self.snapshot = runner, snapshot
        self.servers = {s["id"]: s for s in snapshot["servers"]}
        self.ready = {}
        self.private_ready = {}
        self.deviations = {}

    async def run(self, socket, *args):
        return await local_command([binary(), "-N", "-S", socket, *args])

    def shell(self, socket, pane, cwd=None):
        marker = shlex.join([binary(), "-N", "-S", socket, "set-option", "-p", "-t"]) + ' "$TMUX_PANE" @fb-pane ' + shlex.quote(pane)
        startup = directory_bootstrap(cwd, "/bin/sh -l") if cwd else "exec /bin/sh -l"
        return shlex.join(["/bin/sh", "-c", marker + "; " + startup])

    async def original(self, server, session):
        # Local PID + kernel start time names a server without trusting a reused socket.
        # Remote attachment requires a read-only identity probe with current auth.
        if not server["local"]:
            args = server["args"]
            command = shlex.join(["tmux", "-N", "-S", server["socket"], "display-message", "-p", "-t", session["id"],
                                  "#{host}\t#{socket_path}\t#{pid}\t#{start_time}\t#{session_id}\t#{session_created}"])
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=2",
                    "-o", "ConnectionAttempts=1", "-o", "RemoteCommand=none", "-o", "PermitLocalCommand=no", *args[:-1], "--", args[-1], command]
            try:
                host, socket, pid, started, sid, born = (await local_command(argv)).strip().split("\t")
                digest = hashlib.sha256(json.dumps([host, args], separators=(",", ":")).encode()).hexdigest()[:16]
                identity = f"tmux:{host.split('.')[0].lower()}:{socket}:{digest}"
                if identity != server["id"] or (int(pid), int(started), sid, int(born)) != (server["pid"], server["started"], session["id"], session["created"]):
                    raise UserError("Remote tmux identity changed; server preserved")
            except Exception as e:
                raise UserError("Remote tmux could not be verified; authenticate manually and retry. Lost remote servers require manual recovery") from e
            return {"socket": socket, "session": sid, "panes": {p["id"]: p["id"] for w in session["windows"] for p in w["panes"]},
                    "live": True, "args": args, "proof": "\t".join([host, socket, pid, started, sid, born])}
        if not server["started"] or procinfo.proc_start(server["pid"]) != server["started"]:
            return None
        try:
            text = await self.run(server["socket"], "display-message", "-p", "-t", session["id"], "#{pid}\t#{session_id}\t#{session_created}")
            pid, sid, born = text.strip().split("\t")
        except Exception:
            return None
        if (int(pid), sid, int(born)) != (server["pid"], session["id"], session["created"]):
            return None
        return {"socket": server["socket"], "session": sid, "panes": {p["id"]: p["id"] for w in session["windows"] for p in w["panes"]},
                "live": True}

    async def private(self, server, session):
        job = self.runner.job
        # /tmp keeps Unix paths short; random durable job ID separates this server
        # from all existing sockets. Never read an existing user's tmux configuration.
        folder = Path("/tmp") / f"fb-tmux-{os.getuid()}-{job['id']}"
        folder.mkdir(mode=0o700, exist_ok=True)
        if folder.is_symlink() or folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
            raise UserError("Recovery tmux folder is not private")
        socket = str(folder / hashlib.sha256(server["id"].encode()).hexdigest()[:12])
        name = "fb" + session["id"].lstrip("$")
        identity = job["id"] + ":" + session["id"]
        completed = self.runner.job["steps"].get("tmux:" + server["id"] + ":" + session["id"], {}).get("state") == "restored"
        try:
            names = (await self.run(socket, "list-sessions", "-F", "#{session_name}")).splitlines()
        except UserError:
            if not private_absent(socket):
                raise UserError("Private tmux server cannot be verified; preserved")
            names = []
        if not names:
            # Reset the prior generation before any creation; a crash halfway
            # through this durable barrier still leaves the server absent.
            for saved in server["sessions"]:
                step = "tmux:" + server["id"] + ":" + saved["id"]
                if self.runner.job["steps"].get(step, {}).get("state") == "restored":
                    await self.runner.step(step, "pending", "Private tmux server lost; rebuilding recorded shells")
            completed = False
        if completed and names and name not in names:
            raise UserError("Restored tmux session was removed; live graph preserved")
        try:
            actual_name, existing = (await self.run(socket, "display-message", "-p", "-t", name, "#{session_name}\t#{@fb-restore}")).strip().split("\t")
            if actual_name != name:
                raise UserError("Recovery tmux session identity changed")
        except UserError:
            existing = None
        if existing is not None and existing != identity:
            raise UserError("Recovery tmux session identity changed")
        result = {"socket": socket, "session": name, "panes": {}, "live": False}
        complete = existing is not None and completed
        if existing is not None and not complete:
            await self.partial_preflight(socket, name, identity, session)
        for index, window in enumerate(session["windows"]):
            order = ["%" + m.group(1) for m in re.finditer(r"\d+x\d+,\d+,\d+,(\d+)(?=[,}\]]|$)", window["layout"])]
            if set(order) != {p["id"] for p in window["panes"]} or len(order) != len(window["panes"]):
                raise UserError("Invalid tmux leaf layout")
            panes = sorted(window["panes"], key=lambda p: order.index(p["id"]))
            if not panes:
                continue
            target = name + ":" + str(window["index"])
            key = identity + ":" + window["id"]
            if index == 0 and existing is None:
                p = panes[0]
                cwd = self.directory(p)
                width, height = re.match(r"[0-9a-f]+,(\d+)x(\d+)", window["layout"]).groups()
                command = [binary(), "-S", socket, "-f", "/dev/null", "new-session", "-d", "-s", name,
                                     "-c", cwd, "-x", width, "-y", height,
                                     self.shell(socket, p["id"], p.get("cwd")), ";", "set-option", "-t", name, "@fb-restore", identity,
                                     ";", "set-option", "-g", "default-command", "/bin/sh -l",
                                     ";", "set-option", "-g", "default-shell", "/bin/sh",
                                     ";", "set-option", "-w", "-t", name, "@fb-window", key]
                # Creation, identity and nonzero-index movement share one queue.
                if window["index"] != 0:
                    command.extend([";", "move-window", "-s", name + ":0", "-t", target])
                await local_command(command)
                existing = identity
            else:
                try:
                    value = (await self.run(socket, "display-message", "-p", "-t", target, "#{window_index}\t#{@fb-window}")).rstrip("\n")
                    actual_index, marker = value.split("\t")
                    if int(actual_index) != window["index"]:
                        raise UserError("Recovery tmux window identity changed")
                except UserError:
                    marker = None
                if marker is None:
                    if complete:
                        raise UserError("Restored tmux topology changed; live windows preserved")
                    await self.run(socket, "new-window", "-d", "-t", target, "-c", self.directory(panes[0]), self.shell(socket, panes[0]["id"], panes[0].get("cwd")),
                                   ";", "set-option", "-w", "-t", target, "@fb-window", key)
                elif marker != key:
                    raise UserError("Recovery tmux window identity changed")
            text = await self.marked(socket, target)
            found = {}
            for line in text.splitlines():
                new, old = line.split("\t")
                if old in found or old not in {p["id"] for p in panes}:
                    raise UserError("Recovery tmux panes were changed; preserved")
                found[old] = new
            previous = panes[0]["id"]
            for p in panes:
                if p["id"] not in found:
                    if complete:
                        raise UserError("Restored tmux topology changed; live panes preserved")
                    # One queued tmux command group creates and labels a pane even
                    # if the API bridge exits before its journal write completes.
                    await self.run(socket, "split-window", "-d", "-t", found[previous], "-c", self.directory(p), self.shell(socket, p["id"], p.get("cwd")),
                                   ";", "select-layout", "-t", target, "tiled")
                    text = await self.marked(socket, target)
                    found = dict((old, new) for new, old in (row.split("\t") for row in text.splitlines()))
                previous = p["id"]
            if not complete:
                await self.run(socket, "select-layout", "-t", target, remap_layout(window["layout"], found))
            if not complete and window["active"] in found:
                await self.run(socket, "select-pane", "-t", found[window["active"]])
            result["panes"].update(found)
        for pane in (p for w in session["windows"] for p in w["panes"]):
            if pane["id"] in self.deviations:
                await self.runner.step("tmux-directory:" + server["id"] + ":" + pane["id"], "deviation", self.deviations[pane["id"]])
        await self.runner.step("tmux:" + server["id"] + ":" + session["id"], "restored", "Private tmux graph recreated with shells only")
        return result

    async def partial_preflight(self, socket, name, identity, session):
        """Creation provenance never authorizes overwriting subsequent user state."""
        expected = {w["index"]: w for w in session["windows"]}
        windows = await self.run(socket, "list-windows", "-t", name, "-F", "#{window_index}\t#{@fb-window}\t#{window_layout}")
        observed = {}
        for row in windows.splitlines():
            index, marker, layout = row.split("\t")
            saved = expected.get(int(index))
            if not saved or marker != identity + ":" + saved["id"]:
                raise UserError("Recovery tmux window topology changed; preserved")
            observed[int(index)] = layout
        rows = await self.run(socket, "list-panes", "-s", "-t", name, "-F",
                              "#{window_index}\t#{pane_id}\t#{@fb-pane}\t#{pane_current_path}\t#{pane_current_command}")
        mapped = {}
        for row in rows.splitlines():
            index, new, old, cwd, command = row.split("\t")
            saved = expected.get(int(index))
            pane = next((p for p in saved["panes"] if p["id"] == old), None) if saved else None
            found = mapped.setdefault(int(index), {})
            if not pane or old in found:
                raise UserError("Recovery tmux pane topology changed; preserved")
            expected_cwd = pane["cwd"] if directory_available(pane.get("cwd")) else fallback_directory()
            if command not in SHELLS or cwd != expected_cwd:
                raise UserError("Recovery tmux pane is busy or its directory changed; preserved")
            found[old] = new
        for index, layout in observed.items():
            found, saved = mapped.get(index, {}), expected[index]
            if len(found) == len(saved["panes"]) and layout != remap_layout(saved["layout"], found):
                raise UserError("Recovery tmux layout changed or completion is ambiguous; preserved")

    async def marked(self, socket, target):
        import asyncio
        for _ in range(30):
            text = await self.run(socket, "list-panes", "-t", target, "-F", "#{pane_id}\t#{@fb-pane}")
            if all(len(row.split("\t")) == 2 and row.split("\t")[1] for row in text.splitlines()):
                return text
            await asyncio.sleep(0.1)
        raise UserError("tmux pane creation identity unavailable; preserved for retry")

    def directory(self, pane):
        cwd = pane.get("cwd")
        if not directory_available(cwd):
            self.deviations[pane["id"]] = "Recorded tmux directory unavailable; shell opened in home or filesystem root"
        # The shell enters the recorded directory after launch, avoiding the -c race.
        return fallback_directory()

    async def connection(self, recipe):
        key = recipe["server"], recipe["session"]
        if key not in self.ready:
            server = self.servers.get(key[0])
            session = next((s for s in server["sessions"] if s["id"] == key[1]), None) if server else None
            if not session:
                raise UserError("Recorded tmux server graph is unavailable")
            self.ready[key] = await self.original(server, session) or await self.group(server, session)
        return self.ready[key]

    async def group(self, server, session):
        """Rebuild authoritative groups once while preserving each session identity."""
        members = [s for s in server["sessions"] if session.get("group") and s.get("group") == session["group"]]
        for member in members:
            if await self.original(server, member):
                raise UserError("Original tmux group changed; surviving sessions and applications preserved")
        lead = min(members, key=lambda s: int(s["id"].lstrip("$"))) if members else session
        key = server["id"], lead["id"]
        if key not in self.private_ready:
            self.private_ready[key] = await self.private(server, lead)
        result = self.private_ready[key]
        if session["id"] == lead["id"]:
            return result
        name = "fb" + session["id"].lstrip("$")
        identity = self.runner.job["id"] + ":" + session["id"]
        names = (await self.run(result["socket"], "list-sessions", "-F", "#{session_name}")).splitlines()
        step = "tmux:" + server["id"] + ":" + session["id"]
        if name not in names:
            if self.runner.job["steps"].get(step, {}).get("state") == "restored":
                raise UserError("Restored tmux session was removed; live graph preserved")
            await self.run(result["socket"], "new-session", "-d", "-s", name, "-t", result["session"],
                           ";", "set-option", "-t", name, "@fb-restore", identity)
        actual, marker, group = (await self.run(result["socket"], "display-message", "-p", "-t", name,
                                               "#{session_name}\t#{@fb-restore}\t#{session_group}")).strip().split("\t")
        lead_group = (await self.run(result["socket"], "display-message", "-p", "-t", result["session"], "#{session_group}")).strip()
        if (actual, marker) != (name, identity) or not group or group != lead_group:
            raise UserError("Recovery tmux group identity changed; live graph preserved")
        await self.runner.step(step, "restored", "Private tmux session shares its recorded group's graph")
        return {**result, "session": name}

    async def command(self, recipe, client_id=None):
        result = await self.connection(recipe)
        pane = result["panes"].get(recipe["pane"])
        if not pane:
            raise UserError("Recorded tmux pane is no longer available")
        if recipe["control"]:
            if result.get("args"):
                probe = shlex.join(["tmux", "-N", "-S", result["socket"], "display-message", "-p", "-t", result["session"],
                                    "#{host}\t#{socket_path}\t#{pid}\t#{start_time}\t#{session_id}\t#{session_created}"])
                attach = shlex.join(["tmux", "-N", "-S", result["socket"], "-CC", "attach-session", "-E", "-t", result["session"]])
                command = f'if [ "$({probe})" = {shlex.quote(result["proof"])} ]; then exec {attach}; else printf "%s\\n" "Remote tmux identity changed; retry required" >&2; exit 1; fi'
                return ssh_command(result["args"], command=command)
            return shlex.join([binary(), "-N", "-S", result["socket"], "-CC", "attach-session", "-E", "-t", result["session"]])
        if result.get("args"):
            raise UserError("Plain remote tmux recipe unavailable; use the recorded control-mode connection")
        # A grouped session shares the running windows but keeps this client's
        # selected window independent. active-pane + select-pane acts on this
        # client, preserving the window's active pane used by other clients.
        identity = self.runner.job["id"] + ":" + (client_id or recipe["session"] + ":" + recipe["pane"])
        name = "fbr" + hashlib.sha256((recipe["server"] + ":" + identity).encode()).hexdigest()[:24]
        names = (await self.run(result["socket"], "list-sessions", "-F", "#{session_name}")).splitlines()
        value = await self.run(result["socket"], "display-message", "-p", "-t", name, "#{session_name}\t#{@fb-client}") if name in names else None
        if value is None:
            await self.run(result["socket"], "new-session", "-d", "-s", name, "-t", result["session"],
                           ";", "set-option", "-t", name, "@fb-client", identity)
        elif value.rstrip("\n").split("\t") != [name, identity]:
            raise UserError("tmux recovery client identity changed")
        wid = (await self.run(result["socket"], "display-message", "-p", "-t", pane, "#{window_id}")).strip()
        return shlex.join([binary(), "-N", "-S", result["socket"], "attach-session", "-E", "-f", "active-pane", "-t", name + ":" + wid,
                           ";", "select-pane", "-t", pane])
