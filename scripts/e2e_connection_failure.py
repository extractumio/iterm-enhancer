#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Owned generated SSH failure; a private stub handles argv without network/config reads."""
import asyncio
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run():
    from e2e_isolated import runtime
    with tempfile.TemporaryDirectory(prefix="fbc-", dir="/tmp") as folder:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, FB_APP_DIR=folder, FB_PORT=str(port), FB_BIN=str(REPO / "fbd/target/debug/fbd"),
                   FB_AUTO_TOOLBELT="0", FB_BUILD_ID="connection-failure-test")
        subprocess.run([runtime(), __file__, "--child"], env=env, check=True, timeout=120)


def child():
    from e2e_isolated import require_private_state
    require_private_state()
    import iterm2
    from unittest.mock import patch
    sys.path.insert(0, str(REPO / "bridge"))
    from fbbridge.backend import Backend
    from fbbridge.common import APP_DIR, UserError
    from fbbridge.recovery_capture import Capture, epoch
    from fbbridge.recovery_lifecycle import Lifecycle
    from fbbridge.recovery_runner import Runner
    from fbbridge.recovery_startup import Startup
    from fbbridge.recovery_recipes import ssh_command
    from e2e_restore_platform import profile

    async def main(conn):
        app = await iterm2.async_get_app(conn)
        previous = app.current_terminal_window
        backend = Backend()
        backend.start(new_token=True)
        owned, marker, observer = set(), None, None
        async def windows():
            await app.async_refresh()
            result = []
            for w in app.terminal_windows:
                names = [await s.async_get_variable("profileName") for t in w.tabs for s in t.all_sessions]
                if w.window_id in owned or marker and marker in names:
                    owned.add(w.window_id)
                    result.append(w)
            return result
        try:
            assert await asyncio.to_thread(backend.wait_ready)
            from e2e_common import call
            commands = asyncio.Queue()
            backend.listen_commands(asyncio.get_running_loop(), commands)
            await asyncio.sleep(0.2)
            source_window = await iterm2.Window.async_create(conn, profile_customizations=profile(APP_DIR, "iterm-enhancer Test connection source"))
            owned.add(source_window.window_id)
            capture = Capture(conn, app, type("Windows", (), {"viewer_id":None})(), backend, window_ids=owned)
            await Startup(capture, Runner(capture)).initialize()
            snapshot = await capture.snapshot()
            pane = snapshot["windows"][0]["tabs"][0]["panes"][0]
            pane.update(connection={"kind":"ssh", "args":["devbox.example"]}, cwd=None, cwd_status="unknown")
            saved = await capture.call("POST", "/internal/recovery/capture", {"snapshot":snapshot,"force":True})
            selected = saved["id"]
            marker = f"iterm-enhancer Restore {selected}:{pane['id']}"
            await source_window.async_close(force=True)  # simulate loss before attaching observer
            await app.async_refresh()
            observer = Lifecycle(capture)
            capture.lifecycle = observer
            await observer.poll()
            # The exact generated SSH argv runs against this stub; system ssh never runs.
            binary = APP_DIR / "bin"
            binary.mkdir()
            stub = binary / "ssh"
            stub.write_text("#!/bin/sh\nsleep 1\nexit 255\n")
            stub.chmod(0o700)
            generated = ssh_command(pane["connection"]["args"])
            command = shlex.join(["/usr/bin/env", "PATH=" + str(binary) + ":/usr/bin:/bin", *shlex.split(generated)])
            async def wait_failure():
                for _ in range(40):
                    await windows()  # adopt only our exact creation marker before scoped inventory
                    await observer.poll()
                    if observer.failed:
                        return
                    await asyncio.sleep(0.1)
                raise AssertionError("owned generated connection failure not observed")
            runner = Runner(capture)
            async def restore():
                await capture.call("POST", "/internal/state", {"key":"owned-test","bridge":True})
                status, job = await asyncio.to_thread(call, "POST", "/api/recovery/restore", {"snapshot":selected})
                assert status == 200
                await asyncio.wait_for(commands.get(), 5)
                with patch("fbbridge.recovery_native.ssh_command", return_value=command):
                    assert await runner.run(job["id"])
            await restore()
            await wait_failure()
            assert len(await windows()) == 1, "failed diagnostics must remain available"
            result = await capture.call("GET", "/internal/recovery")
            assert pane["id"] not in result["retired"]
            try:
                await capture.save()
                raise AssertionError("failed connection overwrote previous checkpoint")
            except UserError as error:
                assert "connection exited unsuccessfully" in str(error)
            print("PASS generated SSH exit255 retains diagnostics, source eligibility and pauses capture", flush=True)
            await restore()
            await wait_failure()
            assert len(await windows()) == 2, "explicit Retry creates one controlled attempt"
            assert pane["id"] not in (await capture.call("GET", "/internal/recovery"))["retired"]
            print("PASS explicit Retry remains possible after failed generated connection", flush=True)
            observer.watch.close()
            observer = None
            backend.stop()
            os.environ["FB_BUILD_ID"] = "connection-failure-upgrade"
            backend.start()
            assert await asyncio.to_thread(backend.wait_ready)
            new_capture = Capture(conn, app, capture.windows, backend, window_ids=owned)
            actual = await asyncio.to_thread(epoch)
            _, pid, started = actual.rsplit(":", 2)
            new_capture.epoch = "test-boot:owned-connection-2"
            observer = Lifecycle(new_capture)
            observer.process = int(pid), int(started)
            new_capture.lifecycle = observer
            with patch("fbbridge.recovery_native.ssh_command", return_value=command):
                await Startup(new_capture, Runner(new_capture)).initialize()
            result = await new_capture.call("GET", "/internal/recovery")
            assert result["startup"]["snapshot"] == selected
            assert len(await windows()) == 3, "next process uses preserved connection source once"
            print("PASS upgrade and next process epoch keep failed connection recoverable", flush=True)
        finally:
            if observer:
                observer.watch.close()
            for window in await windows():
                await window.async_close(force=True)
            assert not await windows(), "owned connection windows remain after cleanup"
            if previous and app.get_window_by_id(previous.window_id):
                await previous.async_activate()
            backend.stop()

    async def checked(conn):
        try:
            await main(conn)
        except Exception as error:
            print(f"FAIL {type(error).__name__}: {error}", flush=True)
            raise SystemExit(1) from error
    iterm2.run_until_complete(checked)


if __name__ == "__main__":
    child() if "--child" in sys.argv else run()
