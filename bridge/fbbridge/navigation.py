# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Reuse iTerm2's native searchable session switcher (AC-45)."""
import iterm2

from .common import UserError


async def open_quickly(conn):
    try:
        item = iterm2.MainMenu.View.OPEN_QUICKLY
        state = await iterm2.MainMenu.async_get_menu_item_state(conn, item)
        if not state.enabled:
            raise UserError("Open Quickly is unavailable")
        await iterm2.MainMenu.async_select_menu_item(conn, item)
    except Exception as e:
        raise UserError("Open Quickly is unavailable") from e
