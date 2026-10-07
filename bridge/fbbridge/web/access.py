# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Web access inside the bridge (AC-52). Off unless switched on (CLI `iterm-filebrowser web on`,
or the panel's menu): the bridge follows web.json, starts or stops the server, and tells fbd
the state so panels can show it. fbd itself stays on 127.0.0.1; the web server reaches it as a
proxy that adds its token and lets only file work through (AC-53)."""
import asyncio
import json

from ..common import log
from ..resolve import resolve
from . import config, httpd
from .net import lan_addresses, urls
from .site import Site

FOLLOW_EVERY = 2         # seconds between looks at web.json
REPORT_EVERY = 30        # seconds between status reports (a restarted fbd forgets them)


class WebAccess:
    def __init__(self, conn, app, post, remotes):
        self.conn, self.app, self.post, self.remotes = conn, app, post, remotes
        self.server = self.site = self.hub_task = None
        self.running = None      # the settings the server runs with
        self.seen = object()     # web.json's mtime when last applied
        self.cfg, self.error, self.urls = None, None, []

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
        """The panel's menu: switch on or off, as `iterm-filebrowser web on|off` does."""
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
        if cfg and cfg["enabled"] and not cfg["password"]:
            self.error = "Set a password first: iterm-filebrowser web password"
        await self.report()

    async def start(self, cfg, settings):
        try:
            verify = config.verifier(cfg["password"])
        except (ValueError, TypeError) as e:
            self.error = f"web.json holds a broken password; set it again: iterm-filebrowser web password ({e})"
            log(f"web: {self.error}")
            return
        addresses = await asyncio.get_running_loop().run_in_executor(None, lan_addresses)
        site = Site(self.conn, self.app, cfg, verify, self.files_of, addresses)
        try:
            self.server = await httpd.serve(site.handle, cfg["host"], cfg["port"], site.body_limit)
        except OSError as e:
            self.error = f"Port {cfg['port']} is not available ({e.strerror}); choose another: iterm-filebrowser web on --port N"
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
            return {"error": f"Files on {place['remote']['name']} need the File Browser's helper there: "
                             f"enable the host in the Files panel on the Mac."}
        if not place["cwd"]:
            return {"error": f"This session's folder is not known yet ({r.get('note') or r.get('mode')})."}
        return {"key": r["key"], "cwd": place["cwd"], "host": place["host"]}
