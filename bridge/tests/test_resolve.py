# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-37 / AC-12: what a tmux pane is, and what the bridge agrees to type, without iTerm2
(its module is stubbed where it is not installed)."""
import sys
import types
import unittest
import asyncio
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge import app, resolve  # noqa: E402
from fbbridge.common import LOCAL_HOST  # noqa: E402


def line(host, path="/Users/alex/.ssh", cmd="zsh"):
    return f"{host}\t/tmp/tmux-501/default\t%3\t{path}\t{cmd}"


class ResolveTest(unittest.TestCase):
    def test_a_local_tmux_pane(self):
        r = resolve.tmux_result("tmux -CC", line(LOCAL_HOST))
        self.assertEqual((r["mode"], r["cwd"]), ("tmux -CC", "/Users/alex/.ssh"))

    def test_a_host_named_like_this_mac_behind_ssh_is_remote(self):
        r = resolve.tmux_result("tmux -CC", line(f"{LOCAL_HOST}.evil.example"), via_ssh=True)
        self.assertEqual(r["mode"], "remote")
        self.assertIsNone(r["cwd"], "never this Mac's folder at a path the host chose")

    def test_another_host_name_is_remote(self):
        self.assertEqual(resolve.tmux_result("tmux -CC", line("devbox"))["mode"], "remote")

    def test_identical_remote_tmux_identities_are_separated_by_destination(self):
        a = resolve.tmux_result("tmux -CC", line("ubuntu"), via_ssh=True, destination="first.example")
        b = resolve.tmux_result("tmux -CC", line("ubuntu"), via_ssh=True, destination="second.example")
        self.assertNotEqual(a["key"], b["key"])
        self.assertEqual(a["key"], "tmux-remote:first.example:/tmp/tmux-501/default:%3")

    def test_the_actual_resolver_passes_the_gateway_destination_into_the_key(self):
        async def run():
            session = types.SimpleNamespace(tab=types.SimpleNamespace(tmux_connection_id=42),
                                            async_get_variable=mock.AsyncMock(side_effect=["client", 3]))
            tc = types.SimpleNamespace(connection_id=42, async_send_command=mock.AsyncMock(return_value=line("ubuntu")))
            with mock.patch.object(resolve.iterm2, "async_get_tmux_connections", mock.AsyncMock(return_value=[tc]), create=True), \
                    mock.patch.object(resolve, "gateway_target", mock.AsyncMock(return_value=["first.example"])):
                return await resolve.resolve(None, session)
        r = asyncio.run(run())
        self.assertTrue(r["key"].startswith("tmux-remote:first.example:"))
        self.assertEqual(r["remote_key"], "first.example")

    def test_paths_keep_tabs_newlines_and_edge_whitespace(self):
        for path in ("/tmp/a\tb", "/tmp/a\nb", "/tmp/ trailing ", "/tmp/a\n"):
            with self.subTest(path=path):
                r = resolve.tmux_result("tmux", line(LOCAL_HOST, path) + "\n")
                self.assertEqual(r["cwd"], path)
                self.assertEqual(r["job"], "zsh")
                self.assertEqual(r["key"], f"tmux:{LOCAL_HOST}:/tmp/tmux-501/default:%3")

    def test_gateway_lookup_recovers_and_follows_a_new_ssh_process(self):
        session = types.SimpleNamespace(session_id="gateway", async_get_variable=mock.AsyncMock(return_value=100))
        connection = types.SimpleNamespace(connection_id=42, owning_session=session)
        with mock.patch.object(resolve, "find_descendant", side_effect=[None, 101, 102]), \
                mock.patch.object(resolve, "proc_argv", side_effect=[["ssh", "first"], ["ssh", "second"]]):
            self.assertIsNone(asyncio.run(resolve.gateway_target(connection)))
            self.assertEqual(asyncio.run(resolve.gateway_target(connection)), ["first"])
            self.assertEqual(asyncio.run(resolve.gateway_target(connection)), ["second"])


class TypableTest(unittest.TestCase):
    def test_only_printable_text_is_typed(self):
        self.assertTrue(app.typable("cd '/Users/alex/my dir'"))
        self.assertTrue(app.typable("naïve 文件 "))
        for bad in ("cd x\r", "a\nb", "\x03", "\x1b[200~", "a\x7fb", "a\x85b", "a\u202eb", "\u200b", "\u2066", "\ufeff", None):
            self.assertFalse(app.typable(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
