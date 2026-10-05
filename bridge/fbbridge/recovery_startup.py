# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Reserve the last durable run before capture; reconcile once per iTerm2 process."""
import asyncio
import time

import iterm2

from .common import UserError, log
from .recovery_capture import epoch, signature


class Startup:
    def __init__(self, capture, runner):
        self.capture, self.runner = capture, runner
        self.attempted = False

    async def inventory(self):
        response = await iterm2.rpc.async_list_sessions(self.capture.conn)
        windows = [iterm2.Window.create_from_proto(self.capture.conn, w)
                   for w in response.list_sessions_response.windows]
        if any(w is None for w in windows):
            raise UserError("Startup terminal inventory incomplete; capture paused")
        return signature(windows)

    async def settle(self):
        started = time.monotonic()
        previous, stable = None, started
        while time.monotonic() - started < 30:
            current = await asyncio.wait_for(self.inventory(), min(5, 30 - (time.monotonic() - started)))
            now = time.monotonic()
            if current != previous:
                previous, stable = current, now
            elif now - stable >= 3:
                return
            await asyncio.sleep(0.5)
        raise UserError("Startup terminal layout did not settle; source preserved and capture paused")

    async def initialize(self):
        capture = self.capture
        if capture.epoch is None:
            capture.epoch = await asyncio.to_thread(epoch)
        body = {"epoch": capture.epoch}
        plan = await capture.call("POST", "/internal/recovery/startup", body)
        if plan.get("skipped") and not capture.startup_settled.is_set():
            # Native reopening is independent; its temporary empty/partial layout
            # must not overwrite the manual source after a normal app relaunch.
            await self.settle()
        if plan["phase"] == "pending":
            status = await capture.call("GET", "/internal/recovery")
            if not status["enabled"]:
                await capture.call("POST", "/internal/recovery/startup/begin", body)
                capture.startup_settled.set()
                capture.ready.set()
                return
            job = status.get("job")
            complete = job and job["snapshot"] == plan["snapshot"] and job.get("epoch") == capture.epoch and job["status"] == "complete"
            if not complete:
                if self.attempted:
                    raise UserError("Automatic recovery interrupted; use Retry to reconcile, or disable automatic recovery")
                await self.settle()
                capture.startup_settled.set()
                job = await capture.call("POST", "/internal/recovery/startup/begin", body)
                if job:
                    self.runner.start(job["id"])
                    self.attempted = True
                    if not await self.runner.task:
                        raise UserError("Automatic recovery interrupted; source preserved and capture paused")
            # finish rechecks the persisted journal: task completion alone is insufficient.
            await capture.call("POST", "/internal/recovery/startup/finish", body)
        capture.startup_settled.set()
        capture.ready.set()

    async def follow(self):
        while not self.capture.ready.is_set():
            try:
                await self.initialize()
            except Exception as e:
                message = str(e) if isinstance(e, UserError) else f"Automatic terminal recovery unavailable ({type(e).__name__})"
                log(message)
                try:
                    await self.capture.call("POST", "/internal/recovery/error", {"message": message})
                except Exception:
                    pass
                await asyncio.sleep(5)
