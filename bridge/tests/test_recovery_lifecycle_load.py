# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Closure tracking asks iTerm2 little (AC-30's load, AC-46/47's proof): a root watched for its
exit is not read again until the watch lets it go or the session comes back; a tmux -CC pane is
not asked its role again; the profile is read every MARKER_EVERY rounds; timeouts back off."""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import recovery_lifecycle  # noqa: E402
from fbbridge.recovery_lifecycle import Lifecycle  # noqa: E402

ROOT = (42, 1, 123)


class Watch:
    """ExitWatch's bookkeeping without kqueue: enroll keeps the root, drain lets exits go."""
    def __init__(self):
        self.sessions, self.exited = {}, []

    async def enroll(self, session, identity):
        self.sessions[identity[0]] = session.session_id, identity
        return True

    def remove(self, pid):
        self.sessions.pop(pid, None)

    def drain(self):
        out = []
        for pid, ok in self.exited:
            observed = self.sessions.pop(pid, None)
            if observed:
                out.append((*observed, ok))
        self.exited = []
        return out

    def close(self):
        pass


def session(sid="pane", role=""):
    names = {"profileName": "Default", "tmuxRole": role}
    return types.SimpleNamespace(session_id=sid, async_get_variable=AsyncMock(side_effect=lambda n: names.get(n, "")))


class LoadTest(unittest.IsolatedAsyncioTestCase):
    def fixture(self, s=None):
        capture = types.SimpleNamespace(epoch="boot:50:100", conn=types.SimpleNamespace(websocket=types.SimpleNamespace(closed=False)),
                                        call=AsyncMock(), windows=types.SimpleNamespace(viewer_id=None), window_ids=None, restoring=True)
        watch = Watch()
        observer = Lifecycle(capture, watch)
        observer.process = (50, 100)
        self.s = s or session()
        observer.inventory = AsyncMock(return_value={"pane": self.s})
        return observer, capture, watch

    async def poll(self, observer, now, identity=ROOT):
        roots = AsyncMock(return_value=identity)
        with patch("fbbridge.recovery_lifecycle.time.monotonic", return_value=now), \
             patch("fbbridge.recovery_lifecycle.procinfo.proc_start", return_value=100), \
             patch("fbbridge.recovery_lifecycle.root_identity", roots):
            await observer.poll()
        return roots.await_count

    def reads(self, name):
        return sum(1 for c in self.s.async_get_variable.await_args_list if c.args[0] == name)

    async def test_a_watched_root_is_not_read_again(self):
        observer, _, watch = self.fixture()
        self.assertEqual(await self.poll(observer, 0), 1)
        self.assertIn(ROOT[0], watch.sessions)
        self.assertEqual([await self.poll(observer, t) for t in (0.5, 1, 1.5)], [0, 0, 0])
        self.assertIn("pane", observer.live)

    async def test_an_exit_the_watch_reports_reads_the_root_again(self):
        observer, capture, watch = self.fixture()
        await self.poll(observer, 0)
        watch.exited = [(ROOT[0], True)]
        self.assertEqual(await self.poll(observer, 1, None), 1)
        await self.poll(observer, 3, None)
        self.assertIn(False, [c.args[2]["revive"] for c in capture.call.call_args_list if c.args[1].endswith("/lifecycle")])

    async def test_a_root_the_watch_let_go_unreported_is_read_again(self):
        observer, _, watch = self.fixture()
        await self.poll(observer, 0)
        watch.sessions.clear()          # a death by a signal: popped, not reported
        self.assertEqual(await self.poll(observer, 1), 1)

    async def test_a_session_that_comes_back_proves_its_root_afresh(self):
        observer, _, _ = self.fixture()
        await self.poll(observer, 0)
        observer.inventory.return_value = {}
        await self.poll(observer, 0.5)
        observer.inventory.return_value = {"pane": self.s}
        self.assertEqual(await self.poll(observer, 1), 1)

    async def test_a_refused_watch_keeps_reading_every_round(self):
        observer, _, watch = self.fixture()
        watch.enroll = AsyncMock(return_value=False)
        self.assertEqual([await self.poll(observer, t) for t in (0, 0.5, 1)], [2, 1, 1])   # the first proves it twice (refused)

    async def test_a_tmux_control_pane_is_asked_its_role_once(self):
        observer, _, _ = self.fixture(session(role="client"))
        await self.poll(observer, 0, None)
        roots = [await self.poll(observer, t, None) for t in (0.5, 1, 1.5)]
        self.assertEqual((self.reads("tmuxRole"), roots), (1, [0, 0, 0]))
        self.assertIn("pane", observer.live)

    async def test_a_root_not_proven_is_tried_again_every_few_rounds_and_at_once_when_back(self):
        observer, _, _ = self.fixture()
        n = recovery_lifecycle.MARKER_EVERY
        tries = [await self.poll(observer, k * 0.5, None) for k in range(2 * n)]
        self.assertEqual((sum(tries), tries[0], tries[n]), (2, 1, 1))
        self.assertEqual(self.reads("tmuxRole"), 2)
        observer.inventory.return_value = {}
        await self.poll(observer, 20, None)
        observer.inventory.return_value = {"pane": self.s}
        self.assertEqual(await self.poll(observer, 20.5, None), 1)

    async def test_the_profile_is_read_every_few_rounds(self):
        observer, _, _ = self.fixture()
        for k in range(2 * recovery_lifecycle.MARKER_EVERY):
            await self.poll(observer, k * 0.5)
        self.assertEqual(self.reads("profileName"), 3)    # the first round, then every MARKER_EVERY
        self.assertEqual(observer.known["pane"], "Default")

    async def test_timeouts_back_off_and_an_answer_resets(self):
        observer, _, _ = self.fixture()
        waits, results = [], [TimeoutError(), TimeoutError(), TimeoutError(), None, asyncio.CancelledError()]

        async def poll():
            r = results.pop(0)
            if r:
                raise r
        observer.poll = poll

        async def sleep(t):
            waits.append(t)
        with patch.object(recovery_lifecycle.asyncio, "sleep", sleep), patch.object(recovery_lifecycle, "log", lambda *a: None):
            with self.assertRaises(asyncio.CancelledError):
                await observer.follow()
        self.assertEqual(waits, [1.0, 2.0, 4.0, 0.5])


if __name__ == "__main__":
    unittest.main()
