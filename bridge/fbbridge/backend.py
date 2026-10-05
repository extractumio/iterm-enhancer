# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The fbd child process and the bridge's HTTP side of it."""
import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

from . import unixhttp
from .common import BUILD, BUILD_DIR, LOG_DIR, PORT, SOCKET, UserError, log


def find_fbd():
    """fbd of this bridge's own build first, so a bridge never starts another build's
    backend after `current` was switched (AC-33)."""
    # never any `fbd` on PATH: an unrelated program of that name would get the bridge secret
    for cand in (os.environ.get("FB_BIN"), str(BUILD_DIR / "fbd"), str(Path.home() / ".iterm-filebrowser/bin/fbd")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise SystemExit("fbd binary not found: run `make install`")


class Backend:
    def __init__(self):
        self.secret = secrets.token_hex(16)
        self.proc = None
        self.failures = 0

    def start(self, new_token=False):
        """Start fbd; `new_token`: the token may have reached another program (AC-07)."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logf = LOG_DIR / "fbd.log"
        if logf.exists() and logf.stat().st_size > 5 << 20:
            logf.replace(LOG_DIR / "fbd.log.1")
        env = dict(os.environ, FB_BRIDGE_SECRET=self.secret, FB_PORT=str(PORT))
        env.pop("FB_NEW_TOKEN", None)
        if new_token:
            env["FB_NEW_TOKEN"] = "1"
        self.proc = subprocess.Popen([find_fbd()], env=env, stdout=subprocess.DEVNULL,
                                     stderr=open(logf, "a"), start_new_session=True)
        log(f"fbd started pid={self.proc.pid} build={BUILD}")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def post(self, path, body):
        """POST to fbd over its socket; ConnectionError unless it answers 2xx."""
        status, data = unixhttp.request(SOCKET, "POST", path, json.dumps(body).encode(),
                                        {"Content-Type": "application/json", "X-FB-Bridge": self.secret})
        if not 200 <= status < 300:
            raise ConnectionError(f"fbd answered {status} to {path}: {data[:200].decode(errors='replace')}")
        return status

    def query(self, method, path, body=None, timeout=10):
        """Structured recovery calls remain on the authenticated private socket."""
        status, data = unixhttp.request(SOCKET, method, path, json.dumps(body).encode() if body is not None else None,
                                       {"Content-Type": "application/json", "X-FB-Bridge": self.secret}, timeout=timeout)
        if not 200 <= status < 300:
            raise ConnectionError(f"Recovery request failed (HTTP {status})")
        return json.loads(data) if data else None

    def answers(self):
        """An fbd of this install runs: it answers on the private socket."""
        try:
            return unixhttp.request(SOCKET, "GET", "/health", timeout=1)[0] == 200
        except OSError:
            return False

    def wait_ready(self, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.post("/internal/state", {"bridge_build": BUILD})  # said once per fbd; the installer waits for it
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
                c = unixhttp.Connection(SOCKET, timeout=None)
                try:
                    c.request("GET", "/internal/commands", headers={"X-FB-Bridge": self.secret})
                    r = c.getresponse()
                    if r.status != 200:
                        raise ConnectionError(f"fbd answered {r.status}")
                    for raw in r:
                        line = raw.decode(errors="replace").rstrip("\n")
                        if line.startswith("data:"):
                            loop.call_soon_threadsafe(queue.put_nowait, json.loads(line[5:]))
                except Exception as e:
                    log(f"commands stream: {e}")
                finally:
                    c.close()
                time.sleep(1)
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        if self.alive():
            self.proc.terminate()  # fbd saves the workspaces on SIGTERM
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
