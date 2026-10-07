#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Owned-window proof for public reconstruction APIs; never saves a live arrangement."""
import asyncio
import tempfile
import uuid
from pathlib import Path

import iterm2


def profile(directory, marker):
    p = iterm2.LocalWriteOnlyProfile()
    for key, value in {"Custom Command": "Yes", "Command": "/bin/bash --noprofile --norc",
                       "Initial Text": "", "Custom Directory": "Yes", "Working Directory": str(directory),
                       "Name": marker, "Triggers": [], "Close Sessions On End": False,
                       "Load Shell Integration Automatically": False}.items():
        p._simple_set(key, value)
    return p


def shape(node):
    if isinstance(node, iterm2.Session):
        return "pane"
    children = [shape(c) for c in node.children]
    return children[0] if len(children) == 1 else {"vertical": node.vertical, "children": children}


async def main(conn):
    app = await iterm2.async_get_app(conn)
    previous = app.current_terminal_window
    owned = []
    with tempfile.TemporaryDirectory(prefix="fbr-proof-") as directory:
        root = Path(directory).resolve()
        marker = "iterm-enhancer Restore " + uuid.uuid4().hex
        try:
            win = await iterm2.Window.async_create(conn, profile_customizations=profile(root, marker))
            if not win:
                raise AssertionError("Owned window was not created")
            owned.append(win)
            await app.async_refresh()
            win = app.get_window_by_id(win.window_id)
            a = win.current_tab.current_session
            b = await a.async_split_pane(vertical=True, profile_customizations=profile(root, marker + "-b"))
            c = await b.async_split_pane(vertical=False, profile_customizations=profile(root, marker + "-c"))
            await app.async_refresh()
            tab = app.get_session_by_id(a.session_id).tab
            expected = {"vertical": True, "children": ["pane", {"vertical": False, "children": ["pane", "pane"]}]}
            assert shape(tab.root) == expected, "Nested native split order is wrong"
            print("PASS public native split tree", flush=True)
            for s in tab.sessions:
                s.preferred_size = iterm2.util.Size(45 if s.session_id == a.session_id else 30, 15)
            await tab.async_update_layout()
            await app.async_refresh()
            tab = app.get_session_by_id(a.session_id).tab
            assert len(tab.sessions) == 3 and shape(tab.root) == expected
            print("PASS preferred cell sizes preserve topology", flush=True)
            frame = await win.async_get_frame()
            frame.size = iterm2.util.Size(900, 650)
            await win.async_set_frame(frame)
            actual = await win.async_get_frame()
            assert abs(actual.size.width - 900) <= 20 and abs(actual.size.height - 650) <= 20
            print("PASS native window frame within cell rounding", flush=True)
            name = await a.async_get_variable("profileName")
            assert name == marker, "Creation-time marker is not discoverable"
            print("PASS creation-time marker discoverable without a journal entry", flush=True)
            extra = await win.async_create_tab(profile_customizations=profile(root, marker + "-tab"))
            await app.async_refresh()
            extra = app.get_tab_by_id(extra.tab_id)
            moved = await extra.async_move_to_window()
            owned.append(moved)
            await app.async_refresh()
            target = app.get_window_by_id(win.window_id)
            await target.async_set_tabs(target.tabs + [app.get_tab_by_id(extra.tab_id)])
            await app.async_refresh()
            assert len(app.get_window_by_id(win.window_id).tabs) == 2
            print("PASS tab movement and ordered grouping", flush=True)
            # Metadata only: inspect these test panes, never another terminal's output.
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
            from fbbridge.resolve import resolve
            for sid in (a.session_id, b.session_id, c.session_id):
                assert (await resolve(conn, app.get_session_by_id(sid)))["cwd"] == str(root)
            print("PASS safely launched panes start in their requested directory", flush=True)
            # The public restart API keeps the original program. Never use it in
            # recovery: prove this limitation with an owned, harmless command.
            from fbbridge.recovery_recipes import launch_profile
            dead = await target.async_create_tab(profile_customizations=profile(root, marker + "-dead"))
            d = dead.current_session
            await d.async_send_text("exit\r", suppress_broadcast=True)
            await asyncio.sleep(0.6)
            await d.async_set_profile_properties(launch_profile(marker + "-restart", "/bin/bash --noprofile --norc", str(root)))
            await d.async_restart(only_if_exited=True)
            await asyncio.sleep(0.6)
            await app.async_refresh()
            d = app.get_tab_by_id(dead.tab_id).current_session
            assert await d.async_get_variable("profileName") != marker + "-restart", "Recheck restart safety: behavior changed"
            print("PASS ended-session restart ignores controlled profile; restorer never invokes restart", flush=True)
        finally:
            current = app.current_terminal_window
            restore_focus = current and current.window_id in {w.window_id for w in owned}
            for w in reversed(owned):
                if app.get_window_by_id(w.window_id):
                    await w.async_close(force=True)
            await app.async_refresh()
            assert all(app.get_window_by_id(w.window_id) is None for w in owned), "owned platform windows remain after cleanup"
            if restore_focus and previous and app.get_window_by_id(previous.window_id):
                await previous.async_activate()


if __name__ == "__main__":
    async def checked(conn):
        try:
            await main(conn)
        except Exception as e:
            print(f"FAIL {type(e).__name__}: {e}", flush=True)
            raise SystemExit(1) from e
    iterm2.run_until_complete(checked)
