# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The update notice (AC-51). Once a day (by the wall clock: a sleeping Mac must not stretch
it, and a failed try repeats within the hour) the bridge asks GitHub which release is the
latest (one HEAD request for the releases page, no data of the user's in it) and tells fbd
when it is newer than this build. Nothing is downloaded or installed: the panel shows the command
the user runs (AC-40). A build that is not a release never asks, and `no-update-check` in
the state folder stops it (the panel's menu writes it)."""
import asyncio
import json
import os
import re
import ssl
import time
import urllib.request

from .common import APP_DIR, BUILD, RELEASES, log

TAG = re.compile(r"v(\d{1,6})\.(\d{1,6})\.(\d{1,6})")
SYSTEM_CA = "/etc/ssl/cert.pem"  # macOS's own bundle: iTerm2's bundled Pythons ship none
FIRST, TICK, EVERY = 30, 3600, 24 * 3600
OFF, SKIPPED, CHECKED = APP_DIR / "no-update-check", APP_DIR / "update-skipped", APP_DIR / "update-checked"


def version(tag):
    m = TAG.fullmatch(tag or "")
    return tuple(map(int, m.groups())) if m else None


def newer(latest, current):
    a, b = version(latest), version(current)
    return bool(a and b and a > b)


def latest_tag(releases=RELEASES, timeout=5):
    """The tag `<releases>/latest` redirects to, or None; only a well-formed tag counts."""
    request = urllib.request.Request(f"{releases}/latest", method="HEAD", headers={"User-Agent": "iterm-enhancer-update-check"})
    context = ssl.create_default_context(cafile=SYSTEM_CA if os.path.isfile(SYSTEM_CA) else None)  # still verified
    with urllib.request.urlopen(request, timeout=timeout, context=context) as r:
        tag = r.geturl().rstrip("/").rsplit("/", 1)[-1]
    return tag if version(tag) else None


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


class Updates:
    def __init__(self, post, current=BUILD, fetch=latest_tag, clock=time.time):
        self.post, self.current, self.fetch, self.clock = post, current, fetch, clock
        self.latest, self.checked = None, 0.0  # the newest release seen (shown or not), and when
        self.sent = None                       # what fbd was last told: a failed post is retried
        try:
            seen = json.loads(CHECKED.read_text())
            self.latest, self.checked = (seen["latest"] if version(seen["latest"]) else None), float(seen["at"])
        except (OSError, ValueError, KeyError, TypeError):
            pass  # a restart (every upgrade) keeps the answer of the last check

    def notice(self):
        """What panels should show: the newer release, unless the user skipped it or switched off."""
        if OFF.exists() or not newer(self.latest, self.current) or _read(SKIPPED) == self.latest:
            return None
        return self.latest

    async def publish(self):
        notice = self.notice()
        self.sent = None
        await asyncio.get_running_loop().run_in_executor(None, self.post, "/internal/update", {"latest": notice})
        self.sent = notice

    async def check(self):
        if OFF.exists() or not version(self.current):
            return
        try:
            self.latest = await asyncio.get_running_loop().run_in_executor(None, self.fetch)
        except Exception as e:  # offline, rate limited, GitHub down, no CA bundle: only the log hears
            log(f"update check failed: {e!r}"[:200])
            return
        self.checked = self.clock()
        try:
            CHECKED.write_text(json.dumps({"at": self.checked, "latest": self.latest}))
        except OSError as e:
            log(f"update check not remembered: {e}")
        await self.publish()

    async def tick(self):
        try:
            if self.clock() - self.checked >= EVERY:
                await self.check()
            elif self.sent != self.notice():  # fbd was away, or a post failed
                await self.publish()
        except Exception as e:  # fbd away: the next tick tells it
            log(f"update notice not sent: {type(e).__name__}")

    async def follow(self):
        await asyncio.sleep(FIRST)
        while True:
            await self.tick()
            await asyncio.sleep(TICK)

    async def choose(self, action, tag=None):
        """The chip's menu: skip one version, stop checking, or check again."""
        if action == "skip" and version(tag):
            SKIPPED.write_text(tag)
        elif action == "off":
            OFF.write_text("")
        elif action == "on":
            OFF.unlink(missing_ok=True)
            return await self.check()
        await self.publish()
