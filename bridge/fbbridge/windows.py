# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iTerm2 windows the bridge manages: the Toolbelt shown in new windows (AC-25) and the
large viewer window (AC-26) with its remembered size (AC-27)."""
import asyncio
import json
import urllib.parse

import iterm2

from .common import APP_DIR, AUTO_TOOLBELT, BASE, UserError, log
from .procinfo import iterm_uptime
from .viewer_profile import BROWSER, NOT_BROWSER, VIEWER_GUID, VIEWER_PROFILE, ensure_browser, install_viewer_profile

STATE_FILE = APP_DIR / "bridge.json"


async def kind_of(session):
    """The "Custom Command" of a session's profile ("Browser" for a browser session)."""
    return (await session.async_get_profile()).all_properties.get("Custom Command")


async def profile_kind(conn):
    """The stored "Custom Command" of the viewer profile (None: iTerm2 has no such profile)."""
    for p in await iterm2.PartialProfile.async_query(conn, guids=[VIEWER_GUID]):
        return (await p.async_get_full_profile()).all_properties.get("Custom Command")
    return None


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
        self.viewer_host = None                                   # whose files it shows (AC-37)
        self.last_width = 0.0
        self.shown = {}                                           # window id → Toolbelt shown (AC-36)
        self.refused = set()                                      # windows whose Toolbelt menu was disabled
        self.state = self._load()

    # ── Toolbelt in new windows ──────────────────────────────────────────────

    async def tick(self, conn):
        ids = {w.window_id for w in self.app.terminal_windows} - {self.viewer_id}  # never a Toolbelt there
        self.pending |= ids - self.known
        self.known |= ids
        self.pending &= ids
        self.shown = {w: v for w, v in self.shown.items() if w in ids}  # closed windows
        self.refused &= ids
        key = self.app.current_terminal_window
        if AUTO_TOOLBELT and key and key.window_id in self.pending:
            # macOS disables the menu while iTerm2 is not the active app or a sheet is open:
            # the window stays pending and is tried on every poll while it is key (AC-25)
            try:
                st = await iterm2.MainMenu.async_get_menu_item_state(conn, "Show Toolbelt")
                if st.checked or st.enabled:
                    if not st.checked:
                        await iterm2.MainMenu.async_select_menu_item(conn, "Show Toolbelt")
                    self.pending.discard(key.window_id)
                    self.shown[key.window_id] = True
                    self.refused.discard(key.window_id)
                    reason = None
                else:
                    reason = "DISABLED"
            except Exception as e:  # disabled between the read and the select
                reason = str(e)
            if reason and key.window_id not in self.refused:
                self.refused.add(key.window_id)
                log(f"toolbelt: {reason} (will retry while the window is key)")
        await self._remember_viewer_frame()

    async def toolbelt_shown(self, conn, window_id, refresh=False):
        """Whether `window_id` (the key window: the menu reports only that one) shows its
        Toolbelt; read when it is first seen as key and on `refresh`. A state from a window
        without one is marked so panels of other windows ignore it (AC-36)."""
        if refresh or window_id not in self.shown:
            try:
                self.shown[window_id] = (await iterm2.MainMenu.async_get_menu_item_state(conn, "Show Toolbelt")).checked
            except Exception as e:  # menu disabled while a sheet is open: assume shown, as before
                log(f"toolbelt state: {e}")
                return self.shown.get(window_id, True)
        return self.shown[window_id]

    def is_viewer(self, session):
        return session is not None and session.session_id == self.viewer_session

    async def adopt_viewer(self):
        """After a bridge restart, find the viewer window by its profile, so it is reused
        (not a terminal window that got the profile while it was not a browser profile)."""
        for w in self.app.terminal_windows:
            s = w.current_tab.current_session if w.current_tab else None
            if (s and await s.async_get_variable("profileName") == VIEWER_PROFILE
                    and await kind_of(s) == BROWSER):
                self.viewer_id, self.viewer_session = w.window_id, s.session_id
                url = (await s.async_get_profile()).all_properties.get("Initial URL", "")
                self.viewer_host = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("host", [None])[0]
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

    async def open_viewer(self, conn, path, code, host=None):
        win = self.app.get_window_by_id(self.viewer_id) if self.viewer_id else None
        if win and self.viewer_host == host:
            await asyncio.get_running_loop().run_in_executor(None, self.backend.post, "/internal/viewer-open", {"path": path, "host": host})
            await win.async_activate()
            return
        if win:  # one window shows one host's files; closing it could drop unsaved edits
            where = self.viewer_host or "this Mac"
            raise UserError(f"The viewer window shows files of {where}: close it to open {host or 'this Mac'}'s files")
        self.viewer_host = host
        await ensure_browser(lambda: profile_kind(conn), lambda: install_viewer_profile(force=True))
        url = f"{BASE}/?v={code}&view={urllib.parse.quote(path)}" + (f"&host={urllib.parse.quote(host)}" if host else "")
        custom = iterm2.LocalWriteOnlyProfile()
        custom._simple_set("Initial URL", url)
        created = await iterm2.Window.async_create(conn, profile=VIEWER_PROFILE, profile_customizations=custom)
        if not created:
            raise UserError("Viewer window failed: iTerm2 did not open a window")
        self.viewer_id = created.window_id  # at once: tick() must not treat it as a terminal window
        self.known.add(created.window_id)
        self.pending.discard(created.window_id)  # tick() may have seen it before this line
        try:
            win = await self._browser_window(created.window_id)
        except Exception:
            self.viewer_id = self.viewer_session = None  # never reuse a window that failed
            raise
        self.viewer_session = win.current_tab.current_session.session_id
        await self._place(win)

    async def _browser_window(self, window_id):
        """The created window once it has its tab, if it holds a browser session; a terminal
        window is closed."""
        win = None
        for _ in range(40):  # the app model learns about the window's tab a moment later
            win = self.app.get_window_by_id(window_id)
            if win and win.current_tab:
                break
            await asyncio.sleep(0.05)
        if not win or not win.current_tab:
            raise UserError("Viewer window failed: the window opened without a tab")
        session = win.current_tab.current_session
        if await kind_of(session) != BROWSER:
            await win.async_close(force=True)
            raise UserError(NOT_BROWSER)
        return win

    async def _place(self, win):
        """At the last viewer frame, or large next to the terminal window the first time."""
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
