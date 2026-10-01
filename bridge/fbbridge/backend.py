# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The fbd child process and the bridge's HTTP side of it."""
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .common import BASE, LOG_DIR, PORT, UserError, log


def find_fbd():
    for cand in (os.environ.get("FB_BIN"), str(Path.home() / ".local/bin/fbd"), shutil.which("fbd")):
        if cand and os.access(cand, os.X_OK):
            return cand
    raise SystemExit("fbd binary not found: run `make install`")


class Backend:
    def __init__(self):
        self.secret = secrets.token_hex(16)
        self.proc = None
        self.failures = 0

    def start(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logf = LOG_DIR / "fbd.log"
        if logf.exists() and logf.stat().st_size > 5 << 20:
            logf.replace(LOG_DIR / "fbd.log.1")
        env = dict(os.environ, FB_BRIDGE_SECRET=self.secret, FB_PORT=str(PORT))
        self.proc = subprocess.Popen([find_fbd()], env=env, stdout=subprocess.DEVNULL,
                                     stderr=open(logf, "a"), start_new_session=True)
        log(f"fbd started pid={self.proc.pid}")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _headers(self):
        return {"X-FB-Bridge": self.secret, "Host": f"127.0.0.1:{PORT}"}

    def post(self, path, body):
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **self._headers()})
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status

    def wait_ready(self, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.post("/internal/state", {})
                return True
            except Exception:
                if not self.alive():
                    return False
                time.sleep(0.1)
        return False

    def report_failure(self, command, exc):
        """Fail loud: log a failed command and show it in the panel that asked (`by`)."""
        action = command.get("action")
        message = str(exc) if isinstance(exc, UserError) else f"{action} failed: {type(exc).__name__}: {exc}"
        log(f"command {action} failed: {message}")
        try:
            self.post("/internal/error", {"message": message, "by": command.get("by")})
        except Exception as e:
            log(f"error not delivered to the panel: {e}")

    def listen_commands(self, loop, queue):
        """Read GET /internal/commands (SSE) in a thread; hand each command to asyncio."""
        def run():
            while True:
                try:
                    req = urllib.request.Request(BASE + "/internal/commands", headers=self._headers())
                    with urllib.request.urlopen(req) as r:
                        for raw in r:
                            line = raw.decode(errors="replace").rstrip("\n")
                            if line.startswith("data:"):
                                loop.call_soon_threadsafe(queue.put_nowait, json.loads(line[5:]))
                except Exception as e:
                    log(f"commands stream: {e}")
                time.sleep(1)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        if self.alive():
            self.proc.terminate()  # fbd saves the workspaces on SIGTERM
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
