# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Whether iTerm2 answers (AC-55): one light request every EVERY seconds while a browser looks;
when its answer is later than STALL seconds every browser is told {"t": "iterm", "ok": false},
and {"ok": true} with how long it took once it comes. iTerm2 answers its API one request at a
time, so another client asking much (a script reading whole histories) holds up the bridge's
screens and keys: the page says so instead of only looking slow."""
import asyncio

from ..common import log
from ..rpcstats import tag

EVERY = 2.0
STALL = 3.0


async def ask(app):
    """The light request: one variable of the app."""
    await app.async_get_variable("effectiveTheme")


async def watch(hub, probe=ask):
    tag("web stall")
    loop = asyncio.get_running_loop()
    while True:
        if any(not c.paused for c in hub.clients):
            start = loop.time()
            task = asyncio.ensure_future(probe(hub.app))
            try:
                await asyncio.wait({task}, timeout=STALL)
                if not task.done():
                    hub.stalled = start
                    hub.tell({"t": "iterm", "ok": False, "ms": round(STALL * 1000)})
                    log("web: iTerm2 is not answering its API")
                    await asyncio.wait({task})
            finally:
                task.cancel()                  # stopped while waiting: no request left behind
            if not task.cancelled() and task.exception():   # an error is an answer: not slow
                log(f"web: iTerm2 check: {task.exception()!r}")
            if hub.stalled is not None:
                took = round((loop.time() - hub.stalled) * 1000)
                hub.stalled = None
                hub.tell({"t": "iterm", "ok": True, "ms": took})
                log(f"web: iTerm2 answers again after {took} ms")
        await asyncio.sleep(EVERY)
