# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""One recovery job shared by all panels, with a durable per-pane report."""
import asyncio
import time
import traceback
from pathlib import Path

import iterm2

from .common import UserError, log
from .recovery_native import NativeRestore
from .recovery_tmux import TmuxRestore


class Runner:
    def __init__(self, capture):
        self.capture = capture
        self.conn, self.app = capture.conn, capture.app
        self.job = self.snapshot = None
        self.task = None

    async def step(self, key, state, message, session=None):
        message = message.encode("utf-8")[:512].decode("utf-8", errors="ignore")
        self.job["steps"][key] = {"state": state, "message": message,
                                  "session": session.session_id if session else None,
                                  "tab": session.tab.tab_id if session and session.tab else None,
                                  "window": session.tab.window.window_id if session and session.tab and session.tab.window else None}
        await self.capture.call("POST", "/internal/recovery/job", self.job)

    def start(self, job_id):
        if self.task and not self.task.done():
            return
        self.task = asyncio.create_task(self.run(job_id))

    async def run(self, job_id):
        async with self.capture.lock:
            self.capture.restoring = True
            try:
                status = await self.capture.call("GET", "/internal/recovery")
                self.job = status.get("job")
                if not self.job or self.job["id"] != job_id or self.job["status"] != "running":
                    raise UserError("Recovery job changed")
                self.snapshot = await self.capture.call("GET", "/internal/recovery/snapshot/" + self.job["snapshot"])
                native = NativeRestore(self, TmuxRestore(self, self.snapshot))
                await native.discover()
                for saved in self.snapshot["windows"]:
                    target = None
                    tabs = []
                    owned = True
                    for tab in saved["tabs"]:
                        try:
                            restored, ours = await asyncio.wait_for(native.tab(tab, target), 120)
                            tabs.append(restored)
                            owned = owned and ours
                            if target is None:
                                target = restored.window
                        except Exception as e:
                            message = str(e) if isinstance(e, UserError) else f"Terminal reconstruction failed ({type(e).__name__}); retry available"
                            for pane in tab["panes"]:
                                await self.step(pane["id"], "failed", message, native.session(pane["id"]))
                            owned = False
                    if target and owned:
                        try:
                            await self.app.async_refresh()
                            target = self.app.get_window_by_id(target.window_id)
                            tabs = [self.app.get_tab_by_id(t.tab_id) for t in tabs]
                            # Moving tabs also closes only source windows left empty by
                            # our own tabs; unrelated tabs/windows are never removed.
                            if len(target.tabs) == len([t for t in tabs if t.window.window_id == target.window_id]):
                                await target.async_set_tabs(tabs)
                            else:
                                raise UserError("Recovery window contains additional tabs; grouping preserved")
                            frame = saved["frame"]
                            await target.async_set_frame(iterm2.util.Frame(iterm2.util.Point(frame["origin"]["x"], frame["origin"]["y"]),
                                                                         iterm2.util.Size(frame["size"]["width"], frame["size"]["height"])))
                            actual = await target.async_get_frame()
                            adjusted = any(abs(observed - expected) > 25 for observed, expected in (
                                (actual.size.width, frame["size"]["width"]), (actual.size.height, frame["size"]["height"]),
                                (actual.origin.x, frame["origin"]["x"]), (actual.origin.y, frame["origin"]["y"])))
                            await target.async_set_fullscreen(saved["fullscreen"])
                            active = next((t for old, t in zip(saved["tabs"], tabs) if old["id"] == saved["active"]), None)
                            if active:
                                await active.async_activate()
                            await self.step("window:" + saved["id"], "deviation" if adjusted else "restored",
                                            "Window frame adjusted by iTerm2 or the available display" if adjusted else "Window geometry and tab order applied")
                        except Exception as e:
                            await self.step("window:" + saved["id"], "deviation", str(e) if isinstance(e, UserError) else "Window grouping or geometry could not be applied")
                for index, warning in enumerate(self.snapshot["warnings"]):
                    await self.step("warning:" + str(index), "deviation", warning)
                self.job["status"] = "complete"
                await self.capture.call("POST", "/internal/recovery/job", self.job)
                return True
            except Exception as e:
                location = ", ".join(f"{Path(f.filename).name}:{f.lineno}" for f in traceback.extract_tb(e.__traceback__))
                log(f"Recovery interrupted ({type(e).__name__}) at {location}")
                if self.job:
                    self.job["status"] = "interrupted"
                    self.job["steps"]["recovery"] = {"state": "failed", "message": f"Recovery interrupted ({type(e).__name__}); click Retry to reconcile existing terminals"}
                    try:
                        await self.capture.call("POST", "/internal/recovery/job", self.job)
                    except Exception:
                        pass
                return False
            finally:
                self.capture.restoring = False

    async def follow(self):
        await self.capture.startup_settled.wait()
        orphan, since = None, 0
        while True:
            try:
                status = await self.capture.call("GET", "/internal/recovery")
                job = status.get("job")
                if job and job["status"] == "running" and (self.task is None or self.task.done()):
                    if orphan != job["id"]:
                        orphan, since = job["id"], time.monotonic()
                    elif time.monotonic() - since >= 15:
                        job["status"] = "interrupted"
                        job["steps"]["recovery"] = {"state": "failed", "message": "Bridge restarted or recovery was interrupted; click Retry"}
                        await self.capture.call("POST", "/internal/recovery/job", job)
                else:
                    orphan = None
            except Exception:
                pass  # backend outage is shown by the main bridge; retry after reconnect
            await asyncio.sleep(5)
