# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Web access inside the bridge (AC-52). Off unless switched on (CLI `iterm-enhancer web on`,
or the panel's menu): the bridge follows web.json, starts or stops the server, and tells fbd
the state so panels can show it. fbd itself stays on 127.0.0.1; the web server reaches it as a
proxy that adds its token and lets only file work through (AC-53)."""
import asyncio
import json
from pathlib import Path

from .. import agentctl
from ..common import log
from ..resolve import resolve
from . import config, httpd
from .net import lan_addresses, urls
from .paste import PasteError
from .site import Site

FOLLOW_EVERY = 2         # seconds between looks at web.json
REPORT_EVERY = 30        # seconds between status reports (a restarted fbd forgets them)


class WebAccess:
    def __init__(self, conn, app, post, remotes, tmux=None):
        self.conn, self.app, self.post, self.remotes, self.tmux = conn, app, post, remotes, tmux
        self.server = self.site = self.hub_task = None
        self.running = None      # the settings the server runs with
        self.seen = object()     # web.json's mtime when last applied
        self.cfg, self.error, self.urls = None, None, []
        self.homes = {}          # host key -> its user's home folder, asked once (for "~/" in the terminal)

    async def follow(self):
        ticks = 0
        while True:
            try:
                if config.mtime() != self.seen:
                    await self.apply()
                elif ticks % (REPORT_EVERY // FOLLOW_EVERY) == 0:
                    await self.report()
            except Exception as e:  # web access must never stop the bridge from following iTerm2
                log(f"web: {type(e).__name__}: {e}")
            ticks += 1
            await asyncio.sleep(FOLLOW_EVERY)

    async def switch(self, on):
        """The panel's menu: switch on or off, as `iterm-enhancer web on|off` does."""
        await asyncio.get_running_loop().run_in_executor(None, lambda: config.update(enabled=on))
        await self.apply()

    async def apply(self):
        self.seen = config.mtime()
        try:
            self.cfg, self.error = config.load(), None
        except ValueError as e:
            self.cfg, self.error = None, str(e)
        cfg = self.cfg
        want = bool(cfg and cfg["enabled"] and cfg["password"])
        settings = json.dumps({k: cfg[k] for k in ("host", "port", "password", "allow_hosts", "history")}, sort_keys=True) if want else None
        if settings != self.running:
            await self.stop()
            if want:
                await self.start(cfg, settings)
        if cfg and not cfg["enabled"]:
            config.SESSIONS.unlink(missing_ok=True)   # after stop: off signs every browser out (a lost phone)
        if cfg and cfg["enabled"] and not cfg["password"]:
            self.error = "Set a password first: iterm-enhancer web password"
        await self.report()

    async def start(self, cfg, settings):
        try:
            verify = config.verifier(cfg["password"])
        except (ValueError, TypeError) as e:
            self.error = f"web.json holds a broken password; set it again: iterm-enhancer web password ({e})"
            log(f"web: {self.error}")
            return
        addresses = await asyncio.get_running_loop().run_in_executor(None, lan_addresses)
        site = Site(self.conn, self.app, cfg, verify, self.files_of, addresses, self.paste_to, self.tmux, config.SESSIONS)
        try:
            self.server = await httpd.serve(site.handle, cfg["host"], cfg["port"], site.body_limit)
        except OSError as e:
            self.error = f"Port {cfg['port']} is not available ({e.strerror}); choose another: iterm-enhancer web on --port N"
            log(f"web: {self.error}")
            return
        self.site, self.running = site, settings
        self.hub_task = asyncio.create_task(site.hub.run())
        self.urls = urls(cfg["host"], cfg["port"], addresses)
        log(f"web access on: {', '.join(self.urls)}")

    async def stop(self):
        if not self.server:
            return
        self.hub_task.cancel()
        await self.site.close()
        self.server.close()                      # also the connections still open (Files, its sockets)
        self.server = self.site = self.hub_task = self.running = None
        self.urls = []
        log("web access off")

    async def report(self):
        status = {"enabled": self.server is not None, "password": bool(self.cfg and self.cfg["password"]),
                  "urls": self.urls, "error": self.error}
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.post, "/internal/web", status)
        except (OSError, ConnectionError) as e:
            log(f"web: status not delivered: {e}")

    async def files_of(self, session):
        """The Files view's pane: its key, folder and host, found as the focused pane's are."""
        r = await resolve(self.conn, session)
        place = self.remotes.place(r)
        if place["remote"] and not place["host"]:
            return {"error": f"Files on {place['remote']['name']} need iterm-enhancer's helper there: "
                             f"enable the host in the Files panel on the Mac."}
        if not place["cwd"]:
            return {"error": f"This session's folder is not known yet ({r.get('note') or r.get('mode')})."}
        home = await self.home_of(place["host"], r.get("ssh")) if place["host"] else str(Path.home())
        return {"key": r["key"], "cwd": place["cwd"], "host": place["host"], "home": home}

    async def home_of(self, key, target):
        """The home folder on an enabled host, asked once over its ssh; None when it does not answer."""
        if key not in self.homes and target:
            try:
                out = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: agentctl.ssh(target, 'printf %s "$HOME"', timeout=10))
                self.homes[key] = out if out.startswith("/") else None
            except agentctl.AgentError as e:
                log(f"web: home on {key}: {e}")
                return None                      # asked again next time
        return self.homes.get(key)

    async def paste_to(self, session):
        """Where a pasted image goes for this pane: None for this Mac, else the host's ssh
        arguments; PasteError for a host whose helper is not enabled (as Files, AC-53)."""
        r = await resolve(self.conn, session)
        place = self.remotes.place(r)
        if not place["remote"]:
            return None
        if not place["host"]:
            raise PasteError(f"Pasting images on {place['remote']['name']} needs iterm-enhancer's helper there: "
                             f"enable the host in the Files panel on the Mac.")
        return r["ssh"]
