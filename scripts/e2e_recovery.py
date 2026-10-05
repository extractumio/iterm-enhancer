#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Owned 15-window recovery fixture; private backend, no live token/preferences/tools."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def run():
    from e2e_isolated import runtime
    with tempfile.TemporaryDirectory(prefix="fbr-", dir="/tmp") as folder:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, FB_APP_DIR=folder, FB_PORT=str(port), FB_BIN=str(REPO / "fbd/target/debug/fbd"),
                   FB_AUTO_TOOLBELT="0", FB_BUILD_ID="recovery-test")
        subprocess.run([runtime(), __file__, "--child"], env=env, check=True, timeout=300)


def child():
    from e2e_isolated import require_private_state
    require_private_state()
    import iterm2
    from unittest.mock import patch
    sys.path.insert(0, str(REPO / "bridge"))
    from fbbridge.backend import Backend
    from fbbridge.common import APP_DIR
    from fbbridge.recovery_capture import Capture, epoch, layout
    from fbbridge.recovery_lifecycle import Lifecycle
    from fbbridge.recovery_native import canonical, translate
    from fbbridge.recovery_runner import Runner
    from fbbridge.recovery_startup import Startup
    import fbbridge.recovery_runner as runner_module
    runner_module.log = print
    from fbbridge.resolve import resolve
    from e2e_restore_platform import profile

    async def main(conn):
        app = await iterm2.async_get_app(conn)
        previous = app.current_terminal_window
        before = {w.window_id for w in app.terminal_windows}
        backend = Backend()
        backend.start(new_token=True)
        owned, originals = set(), []
        job_id = None
        jobs = set()
        observer = None
        async def created_windows():
            found = []
            for w in app.terminal_windows:
                if w.window_id in before:
                    continue
                names = [str(await s.async_get_variable("profileName")) for t in w.tabs for s in t.all_sessions]
                marked = any(name.startswith("File Browser Restore " + identity + ":") for name in names for identity in jobs)
                if w.window_id in owned or marked:
                    found.append(w)
                    owned.add(w.window_id)
            return found
        try:
            assert await asyncio.to_thread(backend.wait_ready)
            from e2e_common import call
            queue = asyncio.Queue()
            backend.listen_commands(asyncio.get_running_loop(), queue)
            await asyncio.sleep(0.3)
            for i in range(15):
                directory = APP_DIR / f"cwd-{i}"
                directory.mkdir()
                w = await iterm2.Window.async_create(conn, profile_customizations=profile(directory, f"File Browser Test {i}"))
                originals.append(w)
                owned.add(w.window_id)
                if i == 0:
                    a = w.current_tab.current_session
                    b = await a.async_split_pane(vertical=True, profile_customizations=profile(directory, "File Browser Test split"))
                    await b.async_split_pane(vertical=False, profile_customizations=profile(directory, "File Browser Test nested"))
                if i == 1:
                    await w.async_create_tab(profile_customizations=profile(directory, "File Browser Test second tab"))
            await app.async_refresh()
            capture = Capture(conn, app, type("Windows", (), {"viewer_id": None})(), backend, window_ids=owned)
            await Startup(capture, Runner(capture)).initialize()
            saved = await capture.save(force=True)
            selected = saved["id"]
            jobs.add(selected)
            snapshot = await capture.call("GET", "/internal/recovery/snapshot/" + selected)
            assert len(snapshot["windows"]) == 15
            assert all(p["cwd_status"] == "known" for w in snapshot["windows"] for t in w["tabs"] for p in t["panes"])
            print("PASS capture 15 owned windows, inactive tab and nested splits", flush=True)
            # Exact saved GUID adoption must not create a single extra terminal.
            runner = Runner(capture)
            async def restore():
                await capture.call("POST", "/internal/state", {"key": "owned-test", "bridge": True})
                status, job = await asyncio.to_thread(call, "POST", "/api/recovery/restore", {"snapshot": selected})
                assert status == 200, (status, job)
                await asyncio.wait_for(queue.get(), 5)
                await runner.run(job["id"])
                result = await capture.call("GET", "/internal/recovery")
                assert result["job"]["status"] == "complete", result["job"]["steps"].get("recovery")
                return result["job"]
            job = await restore()
            job_id = job["id"]
            await app.async_refresh()
            assert {w.window_id for w in app.terminal_windows} == before | owned
            print("PASS identified native adoption creates no duplicates", flush=True)
            for w in originals:
                await w.async_close(force=True)
            await app.async_refresh()
            # Test-only controlled shell avoids any personal startup configuration.
            with patch("fbbridge.recovery_native.shell_command", return_value="/bin/bash --noprofile --norc"):
                job = await restore()
            failed = {k: s["message"] for k, s in job["steps"].items() if s["state"] == "failed"}
            assert not failed, failed
            await app.async_refresh()
            restored = await created_windows()
            assert len(restored) == 15, len(restored)
            reverse = {s["session"]: old for old, s in job["steps"].items() if s.get("session")}
            for old in snapshot["windows"]:
                first = old["tabs"][0]["panes"][0]["id"]
                target = app.get_session_by_id(job["steps"][first]["session"]).tab.window
                assert len(target.tabs) == len(old["tabs"])
                for saved_tab, tab in zip(old["tabs"], target.tabs):
                    assert canonical(translate(layout(tab.root), reverse)) == canonical(saved_tab["tree"])
                    for p in saved_tab["panes"]:
                        s = app.get_session_by_id(job["steps"][p["id"]]["session"])
                        assert (await resolve(conn, s))["cwd"] == p["cwd"]
                frame = await target.async_get_frame()
                assert abs(frame.size.width - old["frame"]["size"]["width"]) <= 25
            print("PASS restore 15 windows with ordered tabs, nested splits, cwd and frames", flush=True)
            identities = {s.session_id for w in restored for t in w.tabs for s in t.all_sessions}
            job = await restore()
            await app.async_refresh()
            again = {s.session_id for w in app.terminal_windows if w.window_id not in before for t in w.tabs for s in t.all_sessions}
            assert identities == again
            print("PASS repeated Restore reconciles without new terminals", flush=True)
            # Drop acknowledgements while retaining creation-time identities.
            job["steps"] = {}
            job["status"] = "interrupted"
            await capture.call("POST", "/internal/recovery/job", job)
            await restore()
            await app.async_refresh()
            assert identities == {s.session_id for w in app.terminal_windows if w.window_id not in before for t in w.tabs for s in t.all_sessions}
            print("PASS missing journal acknowledgements reconcile creation markers", flush=True)
            # Verify a live user edit is preserved even if the profile marker changes.
            sample = next(iter(identities))
            changed = app.get_session_by_id(sample)
            changed_cwd = str(APP_DIR.resolve())
            await changed.async_send_text(f"cd {changed_cwd}\r", suppress_broadcast=True)
            customize = iterm2.LocalWriteOnlyProfile()
            customize._simple_set("Name", "File Browser Test changed appearance")
            await changed.async_set_profile_properties(customize)
            for _ in range(30):
                if (await resolve(conn, changed))["cwd"] == changed_cwd:
                    break
                await asyncio.sleep(0.1)
            assert (await resolve(conn, changed))["cwd"] == changed_cwd, "owned fixture did not change directory"
            await restore()
            await app.async_refresh()
            assert (await resolve(conn, app.get_session_by_id(sample)))["cwd"] == changed_cwd, "retry overwrote a live directory"
            assert identities == {s.session_id for w in app.terminal_windows if w.window_id not in before for t in w.tabs for s in t.all_sessions}
            print("PASS journal GUID preserves a changed profile and cwd without duplication", flush=True)
            # A new process reserves the latest state before its first capture.
            latest = await capture.save(force=True)
            selected = latest["id"]
            jobs.add(selected)
            latest_snapshot = await capture.call("GET", "/internal/recovery/snapshot/" + selected)
            (APP_DIR / "cwd-0").rmdir()
            (APP_DIR / "cwd-14").rmdir()
            for w in list(app.terminal_windows):
                if w.window_id in owned:
                    await w.async_close(force=True)
            await app.async_refresh()

            async def startup(identity):
                c = Capture(conn, app, type("Windows", (), {"viewer_id": None})(), backend, window_ids=owned)
                c.epoch = identity  # Private simulation; never quits the user's iTerm2.
                r = Runner(c)
                start_job = r.start
                def tracked_start(identity):
                    jobs.add(identity)
                    start_job(identity)
                r.start = tracked_start
                with patch("fbbridge.recovery_native.shell_command", return_value="/bin/bash --noprofile --norc"):
                    await Startup(c, r).initialize()
                return c, r

            capture, runner = await startup("test-boot:owned-iterm-2")
            result = await capture.call("GET", "/internal/recovery")
            job_id = result["job"]["id"]
            assert job_id == selected and result["startup"]["phase"] == "done"
            assert any("directory unavailable" in step["message"] for step in result["job"]["steps"].values())
            assert not any(step["state"] == "failed" for step in result["job"]["steps"].values())
            await app.async_refresh()
            automatic = await created_windows()
            assert len(automatic) == 15
            identities = {s.session_id for w in automatic for t in w.tabs for s in t.all_sessions}
            print("PASS automatic startup restores 15 windows and a removed directory falls back with report", flush=True)
            # Simulate a partial crash in the nested tab after fallback shells started.
            nested = next(t for w in latest_snapshot["windows"] for t in w["tabs"] if len(t["panes"]) == 3)
            leaf = nested["panes"][-1]["id"]
            await app.get_session_by_id(result["job"]["steps"][leaf]["session"]).async_close(force=True)
            partial = result["job"]
            partial["status"], partial["steps"] = "interrupted", {}
            await capture.call("POST", "/internal/recovery/job", partial)
            with patch("fbbridge.recovery_native.shell_command", return_value="/bin/bash --noprofile --norc"):
                repaired = await restore()
            assert not any(step["state"] == "failed" for step in repaired["steps"].values()), repaired["steps"]
            await app.async_refresh()
            automatic = await created_windows()
            identities = {s.session_id for w in automatic for t in w.tabs for s in t.all_sessions}
            assert len(identities) == 18 and len(automatic) == 15
            print("PASS partial native retry completes nested splits after missing-directory fallback", flush=True)
            await startup("test-boot:owned-iterm-2")
            await app.async_refresh()
            assert identities == {s.session_id for w in app.terminal_windows if w.window_id in owned for t in w.tabs for s in t.all_sessions}
            print("PASS bridge restart in the same iTerm2 process never repeats automatic recovery", flush=True)
            backend.stop()
            os.environ["FB_BUILD_ID"] = "recovery-test-upgrade"
            backend.start()
            assert await asyncio.to_thread(backend.wait_ready)
            await startup("test-boot:owned-iterm-2")
            await app.async_refresh()
            assert identities == {s.session_id for w in app.terminal_windows if w.window_id in owned for t in w.tabs for s in t.all_sessions}
            print("PASS backend restart and build upgrade preserve completed startup and pane identities", flush=True)
            # Crash after reservation, upgrade before the new bridge begins recovery.
            await capture.save(force=True)
            plan = await capture.call("POST", "/internal/recovery/startup", {"epoch": "test-boot:owned-iterm-3"})
            jobs.add(plan["snapshot"])
            backend.stop()
            backend.start()
            assert await asyncio.to_thread(backend.wait_ready)
            resumed, _ = await startup("test-boot:owned-iterm-3")
            result = await resumed.call("GET", "/internal/recovery")
            assert result["startup"]["snapshot"] == plan["snapshot"]
            assert result["job"]["status"] == "complete"
            await app.async_refresh()
            assert identities == {s.session_id for w in app.terminal_windows if w.window_id in owned for t in w.tabs for s in t.all_sessions}
            print("PASS new-process pending reservation survives upgrade and adopts native panes without duplication", flush=True)
            await resumed.save(force=True)
            observer = Lifecycle(resumed)
            actual_epoch = await asyncio.to_thread(epoch)
            _, pid, started = actual_epoch.rsplit(":", 2)
            observer.process = int(pid), int(started)  # simulated backend epochs, real owned process watches
            await observer.poll()
            exits = []
            drain = observer.watch.drain
            def observed_exits():
                values = drain()
                exits.extend(values)
                return values
            observer.watch.drain = observed_exits
            for code in (0, 7):
                ended = next(s for w in app.terminal_windows if w.window_id in owned and len(w.tabs) == 1
                             for t in w.tabs if len(t.all_sessions) == 1 for s in t.all_sessions)
                assert ended.session_id in observer.identities, "owned root watch enrollment unavailable"
                await ended.async_send_text(f"exit {code}\r", suppress_broadcast=True)
                for _ in range(30):
                    await observer.poll()
                    result = await resumed.call("GET", "/internal/recovery")
                    if ended.session_id in result["retired"]: break
                    await asyncio.sleep(0.1)
                assert ended.session_id in result["retired"]
                assert ended.session_id in {sid for sid, _, _ in exits}, "ordinary root exit status was not observed"
                await app.async_refresh()
                assert app.get_session_by_id(ended.session_id) is None, "controlled shell must close without a Restart prompt"
            print("PASS real exit 0/7 closes controlled panes and durably excludes them before another capture", flush=True)
            closed = next(w for w in app.terminal_windows if w.window_id in owned and len(w.tabs) == 1 and
                          len(w.tabs[0].all_sessions) == 1)
            sid = closed.current_tab.current_session.session_id
            await closed.async_close(force=True)
            for _ in range(30):
                await observer.poll()
                result = await resumed.call("GET", "/internal/recovery")
                if sid in result["retired"]: break
                await asyncio.sleep(0.1)
            assert sid in result["retired"]
            print("PASS explicit GUI window close durably excludes its pane before the next capture", flush=True)
            observer.watch.close()
            observer = None
            next_capture, _ = await startup("test-boot:owned-iterm-4")
            await app.async_refresh()
            history_windows = await created_windows()
            assert len(history_windows) == 12, "ordinary exit/close must not resurrect three closed windows"
            saved = await next_capture.save(force=True)
            logical = await next_capture.call("GET", "/internal/recovery/snapshot/" + saved["id"])
            assert len(logical["windows"]) == 12
            assert ended.session_id not in {p["id"] for w in logical["windows"] for t in w["tabs"] for p in t["panes"]}
            again_capture, _ = await startup("test-boot:owned-iterm-5")
            await app.async_refresh()
            assert len(await created_windows()) == 12
            print("PASS later process epochs and checkpoints never resurrect intentionally terminated windows", flush=True)
            # Simulate normal application loss with our own process and window set;
            # the user's iTerm2 process, profiles and state remain untouched.
            from fbbridge.procinfo import proc_start
            from fbbridge.recovery_quit import QuitWatch
            selected = (await again_capture.save(force=True))["id"]
            if not selected:
                selected = (await again_capture.call("GET", "/internal/recovery"))["entries"][-1]["id"]
            jobs.add(selected)
            process = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.readline()"], stdin=subprocess.PIPE)
            quit_watch = QuitWatch((process.pid, proc_start(process.pid)), lambda: backend.query(
                "POST", "/internal/recovery/exit", {"epoch":again_capture.epoch}, timeout=1))
            try:
                process.stdin.write(b"quit\n")
                process.stdin.flush()
                process.wait(timeout=3)
                assert await asyncio.to_thread(quit_watch.finish)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                quit_watch.close()
            for w in await created_windows():
                await w.async_close(force=True)
            skipped, skipped_runner = await startup("test-boot:owned-iterm-6")
            status = await skipped.call("GET", "/internal/recovery")
            assert status["startup"]["skipped"] and status["enabled"]
            assert status["startup"]["snapshot"] == selected
            assert skipped.ready.is_set() and skipped_runner.task is None
            assert not await created_windows()
            print("PASS owned normal app exit skips same-boot automatic restoration and keeps manual source", flush=True)
            backend.stop()
            backend.start()
            assert await asyncio.to_thread(backend.wait_ready)
            skipped_again, _ = await startup("test-boot:owned-iterm-6")
            assert not await created_windows()
            await skipped.call("POST", "/internal/state", {"key":"owned-test", "bridge":True})
            status_code, job = await asyncio.to_thread(call, "POST", "/api/recovery/restore", {"snapshot":selected})
            assert status_code == 200, (status_code, job)
            await asyncio.wait_for(queue.get(), 5)
            skipped_runner.start(job["id"])
            assert await skipped_runner.task
            await app.async_refresh()
            assert len(await created_windows()) == 12
            print("PASS skipped startup survives backend restart and manual Restore rebuilds only owned windows", flush=True)
            await skipped.save(force=True)
            await skipped.call("POST", "/internal/recovery/exit", {"epoch":skipped.epoch})
            for w in await created_windows():
                await w.async_close(force=True)
            rebooted, _ = await startup("next-test-boot:owned-iterm-7")
            result = await rebooted.call("GET", "/internal/recovery")
            await app.async_refresh()
            assert not result["startup"]["skipped"] and result["job"]["status"] == "complete"
            assert len(await created_windows()) == 12
            print("PASS simulated reboot overrides normal exit and restores the owned last workspace", flush=True)
        finally:
            if observer:
                observer.watch.close()
            await app.async_refresh()
            closing = await created_windows()
            restore_focus = app.current_terminal_window and app.current_terminal_window.window_id in {w.window_id for w in closing}
            for w in closing:
                await w.async_close(force=True)
            await app.async_refresh()
            assert not await created_windows(), "owned recovery test windows remain after cleanup"
            if restore_focus and previous and app.get_window_by_id(previous.window_id):
                await previous.async_activate()
            backend.stop()

    async def checked(conn):
        try:
            await main(conn)
        except Exception as e:
            print(f"FAIL {type(e).__name__}: {e}", flush=True)
            raise SystemExit(1) from e
    iterm2.run_until_complete(checked)


if __name__ == "__main__":
    child() if "--child" in sys.argv else run()
