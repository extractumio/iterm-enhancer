# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-44/45: complete identity inventories and the native navigation menu."""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import app, inventory, navigation, resolve  # noqa: E402


class InventoryTest(unittest.IsolatedAsyncioTestCase):
    def test_dead_static_sessions_purged_after_grace_and_gap_resets_grace(self):
        with patch.object(resolve, "_static", {"closed": (1, "tty1"), "buried": (2, "tty2")}), \
             patch.object(inventory.procinfo, "_tmux_args", {}):
            inv = inventory.Inventory()
            for second in range(0, 60, 5):
                inv.prune({"buried"}, second)
            self.assertIn("closed", resolve._static)
            inv.prune({"buried"}, 60)
            self.assertEqual(set(resolve._static), {"buried"})
            inv.prune(set(), 65)
            inv.prune(set(), 90)
            self.assertIn("buried", resolve._static)
            for second in range(95, 151, 5):
                inv.prune(set(), second)
            self.assertFalse(resolve._static)

    async def test_all_tabs_splits_and_buried_sessions_are_live(self):
        session = lambda sid: types.SimpleNamespace(session_id=sid)
        windows = [types.SimpleNamespace(window_id="minimized", tabs=[
            types.SimpleNamespace(all_sessions=[session("inactive"), session("split")])])]
        model = types.SimpleNamespace(windows=windows, buried_sessions=[session("buried")])
        response = types.SimpleNamespace(list_sessions_response=model)
        async def resolved(_, s):
            return {"key": s.session_id, "mode": "zsh"}
        with patch.object(inventory.iterm2, "rpc", types.SimpleNamespace(async_list_sessions=AsyncMock(return_value=response)), create=True), \
             patch.object(inventory.iterm2, "Window", types.SimpleNamespace(create_from_proto=lambda _, w: w), create=True), \
             patch.object(inventory.iterm2, "Session", lambda _, parent, s: s, create=True), \
             patch.object(inventory.iterm2, "async_get_tmux_connections", AsyncMock(return_value=[]), create=True), \
             patch.object(resolve, "resolve", resolved), \
             patch.object(inventory.Inventory, "prune"):
            value = await inventory.Inventory().snapshot(None)
            with patch.object(resolve, "resolve", AsyncMock(side_effect=RuntimeError("unresolved"))):
                uncertain = await inventory.Inventory().snapshot(None)
                self.assertTrue(uncertain["protect_tmux"])
                self.assertEqual(uncertain["sessions"], value["sessions"])
            with patch.object(inventory.iterm2.Window, "create_from_proto", return_value=None):
                with self.assertRaisesRegex(ValueError, "Incomplete"):
                    await inventory.Inventory().snapshot(None)
        self.assertEqual(value["sessions"], ["buried", "inactive", "split"])
        self.assertEqual(value["windows"], ["minimized"])
        self.assertFalse(value["protect_tmux"])

    async def test_terminal_command_suppresses_broadcast_to_unrelated_panes(self):
        session = types.SimpleNamespace(async_send_text=AsyncMock(), async_activate=AsyncMock())
        model = types.SimpleNamespace(get_session_by_id=lambda sid: session if sid == "owned" else None)
        command = {"session": "owned", "text": "cd /tmp", "key": "owned", "job": "zsh", "intent": "cd"}
        with patch.object(app, "resolve", AsyncMock(return_value={"key": "owned", "job": "zsh", "busy": False})):
            await app.send_command(None, model, command)
        session.async_send_text.assert_awaited_once_with("cd /tmp", suppress_broadcast=True)

    async def test_disabled_and_failed_menu_report_exact_error(self):
        menu = types.SimpleNamespace(View=types.SimpleNamespace(OPEN_QUICKLY="Open Quickly"),
                                     async_get_menu_item_state=AsyncMock(return_value=types.SimpleNamespace(enabled=False)),
                                     async_select_menu_item=AsyncMock())
        with patch.object(navigation.iterm2, "MainMenu", menu, create=True):
            with self.assertRaisesRegex(navigation.UserError, "Open Quickly is unavailable"):
                await navigation.open_quickly(None)
            menu.async_select_menu_item.assert_not_awaited()
            menu.async_get_menu_item_state.return_value.enabled = True
            await navigation.open_quickly(None)
            menu.async_select_menu_item.assert_awaited_once_with(None, "Open Quickly")
            menu.async_select_menu_item.side_effect = RuntimeError("disabled meanwhile")
            with self.assertRaisesRegex(navigation.UserError, "Open Quickly is unavailable"):
                await navigation.open_quickly(None)
