# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-52: a sign-in outlives a restart of the bridge; the file keeps no token, and a new
password, a sign-out or web access switched off ends it."""
import json
import os
import stat
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))

from fbbridge.web import access, auth  # noqa: E402


class SignInsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = Path(self.dir.name) / "web-sessions.json"

    def tearDown(self):
        self.dir.cleanup()

    def auth(self, salt="s1"):
        return auth.Auth(lambda pw: pw == "right one", self.file, salt)

    def test_a_sign_in_outlives_a_restart_and_the_file_holds_no_token(self):
        token = self.auth().check_password("right one", "10.0.0.5", secure=True)
        self.assertTrue(self.auth().check_session(token), "a new bridge knows it")
        raw = self.file.read_text()
        self.assertNotIn(token, raw)
        self.assertEqual(stat.S_IMODE(os.stat(self.file).st_mode), 0o600)
        self.assertIn(auth.digest(token), json.loads(raw)["sessions"])

    def test_a_sign_out_stays_signed_out_after_a_restart(self):
        token = self.auth().check_password("right one", "10.0.0.5")
        self.auth().sign_out(token)
        self.assertFalse(self.auth().check_session(token))

    def test_a_new_password_signs_everyone_out(self):
        token = self.auth("s1").check_password("right one", "10.0.0.5")
        self.assertFalse(self.auth("s2").check_session(token))

    def test_expired_and_surplus_sign_ins_go_the_oldest_first(self):
        now = time.time()
        self.file.write_text(json.dumps({"salt": "s1", "sessions": {"old": now - 1, **{f"k{i}": now + 1000 + i for i in range(150)}}}))
        a = self.auth()
        a.check_password("right one", "10.0.0.5")
        kept = json.loads(self.file.read_text())["sessions"]
        self.assertEqual(len(kept), auth.MAX_SESSIONS)
        self.assertNotIn("old", kept)
        self.assertNotIn("k0", kept, "the one that expires first went")
        self.assertIn("k149", kept)

    def test_an_unreadable_file_starts_empty(self):
        for raw in ("{not json", "[]", json.dumps({"salt": "s1", "sessions": {"a": "soon"}})):
            self.file.write_text(raw)
            with mock.patch.object(auth, "log") as logged:      # said in the log (not the real one)
                a = self.auth()
            self.assertTrue(logged.called, raw)
            self.assertFalse(a.check_session("a"))
            self.assertTrue(a.check_session(a.check_password("right one", "10.0.0.5")), raw)

    def test_switching_web_access_off_forgets_every_sign_in(self):
        token = self.auth().check_password("right one", "10.0.0.5")
        acc = access.WebAccess.__new__(access.WebAccess)
        acc.running, acc.report = None, mock.AsyncMock()
        acc.stop = mock.AsyncMock()
        with mock.patch.object(access.config, "SESSIONS", self.file), \
                mock.patch.object(access.config, "load", return_value={"enabled": False, "password": {"salt": "s1"}}), \
                mock.patch.object(access.config, "mtime", return_value=1):
            import asyncio
            asyncio.run(acc.apply())
        self.assertFalse(self.file.exists())
        self.assertFalse(self.auth().check_session(token))

    def test_a_closed_server_writes_no_sign_in(self):
        a = self.auth()
        a.close()
        a.check_password("right one", "10.0.0.5")
        self.assertFalse(self.file.exists(), "a check that ends after web access went off")

    def test_the_cli_switching_off_signs_everyone_out_without_the_bridge(self):
        self.auth().check_password("right one", "10.0.0.5")
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
        import cli
        from fbbridge.web import config
        cfg = Path(self.dir.name) / "web.json"
        with mock.patch.object(config, "SESSIONS", self.file), mock.patch.object(config, "FILE", cfg), \
                mock.patch.object(config.load, "__defaults__", (cfg,)), mock.patch.object(config.update, "__defaults__", (cfg,)), \
                mock.patch("builtins.print"):
            cli.web("off")
        self.assertFalse(self.file.exists())


if __name__ == "__main__":
    unittest.main()
