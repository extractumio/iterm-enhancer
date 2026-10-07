#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Run iTerm2 checks with private fbd/state, own windows and no tool registration.

    python3 scripts/e2e_isolated.py

Never starts the installed bridge or reads its token; native preference setup is faked
in unit tests, not applied here. Requires iTerm2's API and its installed Python runtime.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def require_private_state():
    """Internal child modes refuse live state before importing/starting the backend."""
    value = os.environ.get("FB_APP_DIR")
    root = Path(os.environ.get("FB_ROOT", Path.home() / ".iterm-enhancer"))
    live = {(root / "state").resolve(), (Path.home() / ".iterm-enhancer/state").resolve()}
    if not value or Path(value).resolve() in live:
        raise SystemExit("Integration child requires an isolated FB_APP_DIR; live state is refused")


def runtime():
    base = Path.home() / "Library/Application Support/iTerm2"
    choices = sorted(base.glob("iterm2env-*/versions/*/bin/python3"),
                     key=lambda p: tuple(int(n) for n in p.parents[1].name.split(".")))
    if not choices:
        raise SystemExit("Install iTerm2's Python runtime before running the integration checks")
    return str(choices[-1])


def run():
    with tempfile.TemporaryDirectory(prefix="fbi-", dir="/tmp") as folder:
        private = Path(folder)
        state = private / "state"
        state.mkdir(mode=0o700)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, FB_APP_DIR=str(state), FB_PORT=str(port),
                   FB_BIN=str(REPO / "fbd/target/debug/fbd"), FB_AUTO_TOOLBELT="0",
                   FB_BUILD_ID="isolated-test", FB_E2E_ISOLATED="1")
        # The private follower does not register tools, edit preferences or take the
        # installed bridge's lock. Its log stays inside this test directory.
        python = runtime()
        with (private / "follower.log").open("w") as log:
            bridge = subprocess.Popen([python, __file__, "--follow"], env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if (state / "token").exists() and (state / "fbd.sock").exists():
                        break
                    if bridge.poll() is not None:
                        raise RuntimeError("Private bridge failed to start")
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Private bridge did not become ready")
                for script in ("e2e_cwd.py", "e2e_terminal.py"):
                    subprocess.run([python, str(REPO / "scripts" / script)], env=env, check=True, timeout=240)
                    if bridge.poll() is not None:
                        raise RuntimeError("Private bridge exited during checks")
                subprocess.run(["bash", str(REPO / "scripts/security_check.sh")], env=env, check=True, timeout=60)
            finally:
                bridge.terminate()
                try:
                    bridge.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    bridge.kill()
                    bridge.wait()


def follow():
    require_private_state()
    import iterm2
    sys.path.insert(0, str(REPO / "bridge"))
    from fbbridge import app as bridge_app
    from fbbridge.common import APP_DIR

    class Model:
        def __init__(self, app):
            self.app, self.ids, self.connections = app, set(), set()

        async def refresh(self, conn):
            try:
                self.ids = set(json.loads((APP_DIR / "test-windows.json").read_text()))
            except FileNotFoundError:
                self.ids = set()
            owned = {s.session_id for w in self.app.terminal_windows if w.window_id in self.ids
                     for t in w.tabs for s in t.all_sessions}
            self.connections = {c.connection_id for c in await iterm2.async_get_tmux_connections(conn)
                                if c.owning_session and c.owning_session.session_id in owned}

        @property
        def terminal_windows(self):
            return [w for w in self.app.terminal_windows if w.window_id in self.ids or
                    any(t.tmux_connection_id in self.connections for t in w.tabs)]

        @property
        def current_terminal_window(self):
            w = self.app.current_terminal_window
            return w if w in self.terminal_windows else None

        def get_session_by_id(self, sid):
            return next((s for w in self.terminal_windows for t in w.tabs for s in t.all_sessions if s.session_id == sid), None)

        def get_window_by_id(self, wid):
            return next((w for w in self.terminal_windows if w.window_id == wid), None)

        async def async_get_variable(self, key):
            return await self.app.async_get_variable(key)

    class Windows:
        viewer_id = None
        async def tick(self, conn):
            pass
        async def toolbelt_shown(self, conn, window_id, refresh=False):
            return True
        def is_viewer(self, session):
            return False

    async def no_hosts(conn):
        return {}

    async def main(conn):
        bridge_app.open_hosts = no_hosts
        backend = bridge_app.backend
        backend.start(new_token=True)
        if not await asyncio.to_thread(backend.wait_ready):
            raise RuntimeError("Private fbd failed to start")
        model = Model(await iterm2.async_get_app(conn))
        windows = Windows()
        commands = asyncio.Queue()
        backend.listen_commands(asyncio.get_running_loop(), commands)
        asyncio.create_task(bridge_app.run_commands(conn, model, windows, commands))
        follower = bridge_app.Follower(conn, model, windows)
        try:
            while True:
                await model.refresh(conn)
                await asyncio.wait_for(follower.poll(), 10)
                await asyncio.sleep(0.5)
        finally:
            backend.stop()

    iterm2.run_until_complete(main)


if __name__ == "__main__":
    follow() if "--follow" in sys.argv else run()
