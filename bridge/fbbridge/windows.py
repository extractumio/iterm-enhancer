# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iTerm2 windows the bridge manages: the Toolbelt shown in new windows (AC-25) and the
large viewer window (AC-26) with its remembered size (AC-27)."""
import asyncio
import json
import urllib.parse
from pathlib import Path

import iterm2

from .common import APP_DIR, AUTO_TOOLBELT, BASE, log
from .procinfo import iterm_uptime

VIEWER_PROFILE = "Files Viewer"
VIEWER_GUID = "9F7C4C2B-5E21-4B0F-A6C2-F11E5B0A0C01"
DYNAMIC_PROFILE = Path.home() / "Library/Application Support/iTerm2/DynamicProfiles/iterm-filebrowser.json"
STATE_FILE = APP_DIR / "bridge.json"


def install_viewer_profile():
    """A browser profile for viewer windows (users need not have one). iTerm2 watches the
    DynamicProfiles folder, so writing the file is enough."""
    profile = {"Profiles": [{"Name": VIEWER_PROFILE, "Guid": VIEWER_GUID, "Custom Command": "Browser",
                             "Show Status Bar": False, "Tags": ["iterm-filebrowser"]}]}
    text = json.dumps(profile, indent=2)
    try:
        if not DYNAMIC_PROFILE.exists() or DYNAMIC_PROFILE.read_text() != text:
            DYNAMIC_PROFILE.parent.mkdir(parents=True, exist_ok=True)
            DYNAMIC_PROFILE.write_text(text)
    except OSError as e:
        log(f"viewer profile not written: {e}")


class Windows:
    def __init__(self, app, backend):
        self.app = app
        self.backend = backend
        existing = {w.window_id for w in app.terminal_windows}
        # windows open before the bridge are left alone, except right after iTerm2 launched
        # (AutoLaunch starts with iTerm2, so its first window predates the bridge)
        fresh_launch = (iterm_uptime() or 1e9) < 30
        self.known = set() if fresh_launch else existing
        self.pending = set()                                      # new windows waiting to become key
        self.viewer_id = None
        self.viewer_session = None
        self.last_width = 0.0
        self.state = self._load()

    # ── Toolbelt in new windows ──────────────────────────────────────────────

    async def tick(self, conn):
        ids = {w.window_id for w in self.app.terminal_windows}
        self.pending |= ids - self.known - {self.viewer_id}
        self.known |= ids
        self.pending &= ids
        self.pending.discard(self.viewer_id)
        key = self.app.current_terminal_window
        if AUTO_TOOLBELT and key and key.window_id in self.pending and key.window_id != self.viewer_id:
            self.pending.discard(key.window_id)
            try:
                st = await iterm2.MainMenu.async_get_menu_item_state(conn, "Show Toolbelt")
                if not st.checked:
                    await iterm2.MainMenu.async_select_menu_item(conn, "Show Toolbelt")
            except Exception as e:  # menu disabled while a sheet is open: try on the next focus
                self.pending.add(key.window_id)
                log(f"toolbelt: {e}")
        await self._remember_viewer_frame()

    def is_viewer(self, session):
        return session is not None and session.session_id == self.viewer_session

    async def adopt_viewer(self):
        """After a bridge restart, find the viewer window by its profile, so it is reused."""
        for w in self.app.terminal_windows:
            s = w.current_tab.current_session if w.current_tab else None
            if s and await s.async_get_variable("profileName") == VIEWER_PROFILE:
                self.viewer_id, self.viewer_session = w.window_id, s.session_id
                self.known.add(w.window_id)
                return

    async def set_default_width(self, conn):
        """A panel's Toolbelt was resized: make it the default, but only from a terminal
        window that shows its Toolbelt, and once per burst (every panel may report)."""
        key = self.app.current_terminal_window
        now = asyncio.get_running_loop().time()
        if not key or key.window_id == self.viewer_id or now - self.last_width < 1.0:
            return
        if (await iterm2.MainMenu.async_get_menu_item_state(conn, "Show Toolbelt")).checked:
            await iterm2.MainMenu.async_select_menu_item(conn, "Set Default Width")
            self.last_width = now

    # ── viewer window ────────────────────────────────────────────────────────

    async def open_viewer(self, conn, path, code):
        win = self.app.get_window_by_id(self.viewer_id) if self.viewer_id else None
        if win:
            await asyncio.get_running_loop().run_in_executor(None, self.backend.post, "/internal/viewer-open", {"path": path})
            await win.async_activate()
            return
        url = f"{BASE}/?v={code}&view={urllib.parse.quote(path)}"
        custom = iterm2.LocalWriteOnlyProfile()
        custom._simple_set("Initial URL", url)
        created = await iterm2.Window.async_create(conn, profile=VIEWER_PROFILE, profile_customizations=custom)
        if not created:
            log("viewer window did not open")
            return
        self.viewer_id = created.window_id
        self.known.add(created.window_id)
        self.pending.discard(created.window_id)  # tick() may have seen it before this line
        win = None
        for _ in range(40):  # the app model learns about the window's tab a moment later
            win = self.app.get_window_by_id(created.window_id)
            if win and win.current_tab:
                break
            await asyncio.sleep(0.05)
        if not win or not win.current_tab:
            log("viewer window has no tab yet")
            return
        self.viewer_session = win.current_tab.current_session.session_id
        frame = await win.async_get_frame()
        saved = self.state.get("viewer_frame")
        if saved:
            frame.origin.x, frame.origin.y, frame.size.width, frame.size.height = saved
        else:  # first time: large, next to the terminal window
            ref = self.app.current_terminal_window
            base = await ref.async_get_frame() if ref and ref.window_id != win.window_id else frame
            frame.origin.x, frame.origin.y = base.origin.x + 40, base.origin.y - 40
            frame.size.width, frame.size.height = max(1200, base.size.width), max(800, base.size.height)
        for attempt in range(5):  # a just-created window may refuse its frame for a moment
            try:
                await win.async_set_frame(frame)
                break
            except Exception:
                await asyncio.sleep(0.2)
        await win.async_activate()

    async def _remember_viewer_frame(self):
        win = self.app.get_window_by_id(self.viewer_id) if self.viewer_id else None
        if not win:
            self.viewer_id = self.viewer_session = None
            return
        f = await win.async_get_frame()
        frame = [f.origin.x, f.origin.y, f.size.width, f.size.height]
        if frame != self.state.get("viewer_frame"):
            self.state["viewer_frame"] = frame
            self._save()

    def _load(self):
        try:
            return json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            STATE_FILE.write_text(json.dumps(self.state))
        except OSError as e:
            log(f"bridge state not saved: {e}")
