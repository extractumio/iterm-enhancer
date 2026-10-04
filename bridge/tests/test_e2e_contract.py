# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Integration checks must fail the command after cleanup and refuse live credentials."""
import ast
import asyncio
import contextlib
import io
import os
import runpy
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def main_of(name, namespace):
    tree = ast.parse((SCRIPTS / name).read_text())
    main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
    exec(compile(ast.Module(body=[main], type_ignores=[]), name, "exec"), namespace)
    return namespace["main"]


class IntegrationDirectoryTest(unittest.TestCase):
    def test_missing_or_live_override_is_refused_before_token_read(self):
        for override in ("", "/test-install/state"):
            common = types.ModuleType("fbbridge.common")
            common.ROOT = Path("/test-install")
            common.APP_DIR = common.ROOT / "state"
            common.PORT = 47821
            with self.subTest(override=override), mock.patch.dict(os.environ, {"FB_APP_DIR": override}), \
                    mock.patch.dict(sys.modules, {"fbbridge.common": common}), \
                    mock.patch.object(Path, "read_text", side_effect=AssertionError("no token read")), \
                    self.assertRaisesRegex(RuntimeError, "isolated FB_APP_DIR"):
                runpy.run_path(str(SCRIPTS / "e2e_common.py"))

    def test_isolated_install_reads_only_its_test_token(self):
        with tempfile.TemporaryDirectory() as directory:
            common = types.ModuleType("fbbridge.common")
            common.ROOT = Path(directory) / "live"
            common.APP_DIR = Path(directory) / "test-state"
            common.PORT = 47822
            common.APP_DIR.mkdir()
            (common.APP_DIR / "token").write_text("test-token")
            with mock.patch.dict(os.environ, {"FB_APP_DIR": str(common.APP_DIR)}), \
                    mock.patch.dict(sys.modules, {"fbbridge.common": common}):
                result = runpy.run_path(str(SCRIPTS / "e2e_common.py"))
            self.assertEqual(result["TOKEN"], "test-token")


class IntegrationStatusTest(unittest.IsolatedAsyncioTestCase):
    def fake_app(self):
        session = types.SimpleNamespace(session_id="own-session", async_send_text=mock.AsyncMock())
        window = types.SimpleNamespace(window_id="own-window", current_tab=types.SimpleNamespace(current_session=session),
                                       async_activate=mock.AsyncMock(), async_close=mock.AsyncMock())
        application = types.SimpleNamespace(terminal_windows=[])

        async def create(conn):
            application.terminal_windows.append(window)
        iterm = types.SimpleNamespace(async_get_app=mock.AsyncMock(return_value=application),
                                     Window=types.SimpleNamespace(async_create=create))
        return session, window, iterm

    async def test_cwd_failure_exits_nonzero_after_closing_own_window(self):
        _, window, iterm = self.fake_app()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "cwd"
            root.mkdir()
            namespace = {
                "iterm2": iterm, "asyncio": types.SimpleNamespace(sleep=mock.AsyncMock()),
                "threading": types.SimpleNamespace(Thread=mock.Mock()), "sse": lambda: None,
                "run_mode": mock.AsyncMock(return_value=False), "last_mode": ["tmux"],
                "ROOT": root, "DIRS": [], "SKIP_CC": True, "os": types.SimpleNamespace(system=mock.Mock()),
            }
            main = main_of("e2e_cwd.py", namespace)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                await main(None)
            self.assertEqual(error.exception.code, 1)
            window.async_close.assert_awaited_once_with(force=True)
            self.assertFalse(root.exists())

    async def test_focus_timeout_exits_nonzero_and_types_nothing(self):
        session, window, iterm = self.fake_app()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.mkdir()
            namespace = {
                "iterm2": iterm, "asyncio": types.SimpleNamespace(sleep=mock.AsyncMock()),
                "TARGET": target, "call": lambda *args: (200, {}),
            }
            main = main_of("e2e_terminal.py", namespace)
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                await main(None)
            self.assertEqual(error.exception.code, 1)
            window.async_close.assert_awaited_once_with(force=True)
            session.async_send_text.assert_not_awaited()
            self.assertFalse(target.exists())

    async def test_every_final_failure_exits_after_the_cleanup_block(self):
        for script in ("e2e_cwd.py", "e2e_terminal.py", "e2e_windows.py"):
            with self.subTest(script=script):
                tree = ast.parse((SCRIPTS / script).read_text())
                main = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
                cleanup = next(node for node in main.body if isinstance(node, ast.Try) and node.finalbody)
                finish = main.body[-2:]
                self.assertGreater(finish[0].lineno, cleanup.end_lineno)
                namespace = {"ok": False, "results": [False]}
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as error:
                    exec(compile(ast.Module(body=finish, type_ignores=[]), script, "exec"), namespace)
                self.assertEqual(error.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
