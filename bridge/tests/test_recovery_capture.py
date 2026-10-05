# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Projected live layouts and conservative process identity under metadata failures."""
import stat
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge.common import UserError
from fbbridge.recovery_capture import Capture
from fbbridge.recovery_liveness import session_state
from fbbridge import procinfo


class LivenessTest(unittest.IsolatedAsyncioTestCase):
    def session(self, **values):
        return types.SimpleNamespace(async_get_variable=AsyncMock(side_effect=lambda k: values.get(k)))

    async def test_fresh_pid_and_matching_controlling_tty_are_live(self):
        session = self.session(pid=42, tty="/dev/owned-tty")
        with patch("fbbridge.recovery_liveness.os.kill") as probe, \
             patch("fbbridge.recovery_liveness.os.stat", return_value=types.SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=123)), \
             patch("fbbridge.recovery_liveness.proc_terminal", return_value=123):
            self.assertEqual(await session_state(session), "live")
        probe.assert_called_once_with(42, 0)

    async def test_dead_pid_is_positive_ended_proof_even_without_tty(self):
        with patch("fbbridge.recovery_liveness.os.kill", side_effect=ProcessLookupError):
            self.assertEqual(await session_state(self.session(pid=42)), "ended")

    async def test_bsd_device_field_is_unsigned_and_matches_signed_stat_device(self):
        raw = bytearray(136)
        raw[108:112] = (0x80000001).to_bytes(4, "little")
        with patch.object(procinfo, "_bsdinfo", return_value=bytes(raw)):
            self.assertEqual(procinfo.proc_terminal(42), 0x80000001)
        with patch("fbbridge.recovery_liveness.os.kill"), \
             patch("fbbridge.recovery_liveness.os.stat", return_value=types.SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=-2147483647)), \
             patch("fbbridge.recovery_liveness.proc_terminal", return_value=0x80000001):
            self.assertEqual(await session_state(self.session(pid=42, tty="/dev/owned-tty")), "live")

    async def test_missing_invalid_pid_permission_and_metadata_error_are_unknown(self):
        with patch("fbbridge.recovery_liveness.os.kill") as probe:
            for pid in (None, 0, -1, True, "invalid"):
                self.assertEqual(await session_state(self.session(pid=pid)), "unknown")
            probe.assert_not_called()
        with patch("fbbridge.recovery_liveness.os.kill", side_effect=PermissionError):
            self.assertEqual(await session_state(self.session(pid=42)), "unknown")
        session = types.SimpleNamespace(async_get_variable=AsyncMock(side_effect=ConnectionError))
        self.assertEqual(await session_state(session), "unknown")

    async def test_reused_restored_pid_missing_or_different_tty_is_unknown(self):
        with patch("fbbridge.recovery_liveness.os.kill"), \
             patch("fbbridge.recovery_liveness.os.stat", return_value=types.SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=123)), \
             patch("fbbridge.recovery_liveness.proc_terminal", return_value=456):
            for tty in (None, "/dev/owned-tty"):
                self.assertEqual(await session_state(self.session(pid=42, tty=tty)), "unknown")

    async def test_control_client_without_a_local_pid_is_live(self):
        with patch("fbbridge.recovery_liveness.os.kill") as probe:
            self.assertEqual(await session_state(self.session(tmuxRole="client")), "live")
            probe.assert_not_called()


class Session:
    def __init__(self, sid):
        self.session_id = sid
        self.grid_size = types.SimpleNamespace(width=80, height=24)


def split(vertical, *children): return types.SimpleNamespace(vertical=vertical, children=list(children))
def tab(tid, root, sessions, active):
    return types.SimpleNamespace(tab_id=tid, root=root, all_sessions=sessions, sessions=sessions,
                                 current_session=active, tmux_connection_id=None)
def window(wid, tabs, active):
    frame = types.SimpleNamespace(dict={"origin": {"x": 0, "y": 0}, "size": {"width": 900, "height": 650}})
    return types.SimpleNamespace(window_id=wid, tabs=tabs, current_tab=active,
        async_get_frame=AsyncMock(return_value=frame), async_get_fullscreen=AsyncMock(return_value=False))


class ProjectedCaptureTest(unittest.IsolatedAsyncioTestCase):
    def fixture(self, windows, states):
        capture = Capture(None, None, types.SimpleNamespace(viewer_id=None), None)
        capture.epoch = "owned-iterm"
        capture.pane = AsyncMock(side_effect=lambda s, _: {"id": s.session_id})
        reply = types.SimpleNamespace(list_sessions_response=types.SimpleNamespace(windows=windows))
        return capture, [
            patch("fbbridge.recovery_capture.iterm2.Session", Session, create=True),
            patch("fbbridge.recovery_capture.iterm2.Window", types.SimpleNamespace(create_from_proto=lambda _, w: w), create=True),
            patch("fbbridge.recovery_capture.iterm2.rpc", types.SimpleNamespace(async_list_sessions=AsyncMock(return_value=reply)), create=True),
            patch("fbbridge.recovery_capture.session_state", AsyncMock(side_effect=lambda s: states.get(s.session_id, "unknown"))),
        ]

    async def test_dead_history_is_projected_out_without_multiplying_across_captures(self):
        a, b, c, d = [Session(k) for k in "abcd"]
        alive = tab("alive", split(True, a, split(False, b, c)), [a, b, c], b)
        dead = tab("dead", d, [d], d)
        mixed = window("mixed", [dead, alive], dead)
        history = window("history", [dead], dead)
        capture, patches = self.fixture([mixed, history], {"a": "live", "b": "ended", "c": "unknown", "d": "ended"})
        with patches[0], patches[1], patches[2], patches[3]:
            for _ in range(2):
                snapshot = await capture.snapshot()
                self.assertEqual(len(snapshot["windows"]), 1)
                saved = snapshot["windows"][0]
                self.assertEqual(saved["active"], "alive")
                self.assertEqual(saved["tabs"][0]["active"], "a")
                self.assertEqual(saved["tabs"][0]["tree"], {"vertical": True, "children": [{"pane": "a"}, {"pane": "c"}]})
                self.assertEqual([p["id"] for p in saved["tabs"][0]["panes"]], ["a", "c"])
                self.assertIsNone(saved["tabs"][0]["panes"][1]["cwd"])
        self.assertEqual(capture.pane.await_count, 2, "only the live process supplies metadata")
        history.async_get_frame.assert_not_awaited()

    async def test_unknown_reused_pid_never_resolves_cwd_or_connection_argv(self):
        s = Session("unknown"); t = tab("tab", s, [s], s); w = window("window", [t], t)
        capture, patches = self.fixture([w], {})
        with patches[0], patches[1], patches[2], patches[3]:
            snapshot = await capture.snapshot()
        capture.pane.assert_not_awaited()
        pane = snapshot["windows"][0]["tabs"][0]["panes"][0]
        self.assertIsNone(pane["cwd"])
        self.assertEqual(pane["cwd_status"], "unknown")

    async def test_hidden_only_survivor_has_a_valid_leaf_and_active_pointer(self):
        ended, hidden = Session("ended"), Session("hidden")
        t = tab("tab", ended, [ended, hidden], ended)
        t.sessions = [ended]
        w = window("window", [t], t)
        capture, patches = self.fixture([w], {"ended": "ended", "hidden": "live"})
        with patches[0], patches[1], patches[2], patches[3]:
            snapshot = await capture.snapshot()
        saved = snapshot["windows"][0]["tabs"][0]
        self.assertEqual(saved["tree"], {"pane": "hidden"})
        self.assertEqual(saved["active"], "hidden")
        self.assertTrue(snapshot["warnings"])

    async def test_layout_change_during_sweep_is_not_committed(self):
        a = Session("a"); t = tab("tab", a, [a], a); w = window("window", [t], t)
        capture, patches = self.fixture([w], {"a": "live"})
        async def pane(*_):
            t.root = split(True, a, Session("late"))
            return {"id": "a"}
        capture.pane.side_effect = pane
        with patches[0], patches[1], patches[2], patches[3], self.assertRaisesRegex(UserError, "layout changed"):
            await capture.snapshot()

    async def test_frame_failure_and_partial_inventory_keep_previous_generation(self):
        a = Session("a"); t = tab("tab", a, [a], a); w = window("window", [t], t)
        capture, patches = self.fixture([w], {"a": "live"})
        w.async_get_frame.side_effect = TimeoutError
        with patches[0], patches[1], patches[2], patches[3], self.assertRaises(TimeoutError):
            await capture.snapshot()
        capture, patches = self.fixture([None], {})
        with patches[0], patches[1], patches[2], patches[3], self.assertRaisesRegex(UserError, "checkpoint incomplete"):
            await capture.snapshot()
