# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-43: private request/result files and a fake selective iTerm2 API."""
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.setdefault("iterm2", types.ModuleType("iterm2"))
from fbbridge import settings, setup_state  # noqa: E402


class Profile:
    def __init__(self, guid, kind="No", integration=False, command=None):
        self.guid = guid
        self.all_properties = {"Guid": guid, "Custom Command": kind, settings.INTEGRATION: integration, "Command": command}
        self.writes = []

    async def _async_simple_set(self, key, value):
        self.writes.append((key, value))
        self.all_properties[key] = value


class SetupTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.prefs = {"OpenArrangementAtStartup": False, "OpenNoWindowsAtStartup": False,
                      "RunJobsInServers": True, "OpenBookmark": True}
        self.writes = []
        self.profiles = [Profile("shell"), Profile("ssh", "SSH"), Profile("web", "Browser"),
                         Profile("app", "Yes", command="coding-agent"),
                         Profile("custom", "Custom Shell", command="/opt/bin/fish"),
                         Profile("bash", "Yes", command="/bin/bash")]
        self.queries = []
        self.fail = None
        self.preference = settings.preference

        async def get(_, key):
            return self.prefs.get(key)

        async def set_pref(_, key, value):
            if key == self.fail:
                raise RuntimeError("Rejected")
            self.writes.append(key)
            self.prefs[key] = value

        async def query(_, guids=None, properties=None):
            self.queries.append(properties)
            return [p for p in self.profiles if guids is None or p.guid in guids]

        for patcher in [patch.object(settings, "preference", get),
                        patch.object(settings.iterm2, "preferences", types.SimpleNamespace(async_set_preference=set_pref), create=True),
                        patch.object(settings.iterm2, "PartialProfile", types.SimpleNamespace(async_query=query), create=True),
                        patch.object(settings.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_shell="/bin/zsh"))]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def apply(self):
        return await settings.apply(None, self.directory)

    async def test_changes_only_requested_settings_and_supported_profiles_once(self):
        self.prefs.update(OpenArrangementAtStartup=True, OpenNoWindowsAtStartup=True, RunJobsInServers=False)
        request_id = setup_state.request(self.directory)
        message = await self.apply()
        self.assertEqual(message["id"], request_id)
        self.assertIn("Restart iTerm2", message["message"])
        self.assertEqual(set(self.writes), {k for k, _, _ in settings.PREFERENCES})
        self.assertTrue(self.prefs["OpenBookmark"], "unrelated profile-window startup preserved")
        self.assertEqual([bool(p.writes) for p in self.profiles], [True, True, False, False, True, False])
        self.assertTrue(all(set(keys) <= set(settings.PROFILE_KEYS) for keys in self.queries))
        self.prefs["RunJobsInServers"] = False
        self.profiles[0].all_properties[settings.INTEGRATION] = False
        await self.apply()
        self.assertFalse(self.prefs["RunJobsInServers"], "a restart is not a preference reconciler")
        self.assertEqual(len(self.profiles[0].writes), 1)

    async def test_noop_is_silent_including_default_enabled_restoration(self):
        self.prefs.pop("RunJobsInServers")
        for p in self.profiles:
            p.all_properties[settings.INTEGRATION] = True
        setup_state.request(self.directory)
        self.assertIsNone(await self.apply())
        self.assertFalse(self.writes)
        self.assertEqual(setup_state.read(self.directory / setup_state.RESULT)["status"], "done")

    async def test_no_request_means_no_reads_or_writes(self):
        self.assertIsNone(await self.apply())
        self.assertFalse(self.queries)
        self.assertFalse(self.writes)

    def test_custom_application_and_script_wrappers_are_excluded(self):
        for command in ("zsh -c 'coding-agent'", "zsh script.sh", "bash -lc 'app'", "fish -C agent"):
            self.assertFalse(settings.supported_shell(command), command)
        self.assertTrue(settings.supported_shell("/opt/bin/zsh -il"))

    async def test_partial_failure_preserves_changes_and_consumes_request(self):
        self.prefs.update(OpenArrangementAtStartup=True, RunJobsInServers=False)
        self.fail = "RunJobsInServers"
        setup_state.request(self.directory)
        message = await self.apply()
        self.assertTrue(message["error"])
        self.assertIn("iTerm2 setup incomplete", message["message"])
        self.assertNotIn("Restart iTerm2", message["message"], "restoration write failed")
        self.assertIn("system window restoration", message["message"])
        self.fail = None
        await self.apply()
        self.assertFalse(self.prefs["RunJobsInServers"], "completed failure is not retried on restart")

    async def test_crash_after_write_keeps_change_notice_and_does_not_repeat_write(self):
        request_id = setup_state.request(self.directory)
        setup_state.write(self.directory / setup_state.RESULT,
                          {"id": request_id, "status": "running", "changes": [], "errors": [],
                           "operations": [{"identity": "RunJobsInServers", "done": False}]})
        message = await self.apply()
        self.assertIn("session restoration", message["message"])
        self.assertNotIn("RunJobsInServers", self.writes)

    async def test_corrupt_old_result_does_not_block_a_fresh_install(self):
        (self.directory / setup_state.RESULT).write_text("{broken")
        setup_state.request(self.directory)
        value = await self.apply()
        self.assertIn("previous setup result", value["message"])
        self.assertTrue(value["error"])
        self.assertEqual(setup_state.read(self.directory / setup_state.RESULT)["status"], "done")
        self.assertTrue(self.profiles[0].all_properties[settings.INTEGRATION])

    def test_private_atomic_request_and_matching_result(self):
        request_id = setup_state.request(self.directory)
        self.assertEqual((self.directory / setup_state.REQUEST).stat().st_mode & 0o777, 0o600)
        setup_state.write(self.directory / setup_state.RESULT,
                          {"id": "older", "status": "done", "changes": []})
        self.assertIsNone(setup_state.wait_result(self.directory, request_id, seconds=0.001))
        result = {"id": request_id, "status": "done", "changes": []}
        setup_state.write(self.directory / setup_state.RESULT, result)
        self.assertEqual(setup_state.wait_result(self.directory, request_id), result)
        self.assertFalse(list(self.directory.glob(".*.tmp")))

    async def test_raw_preference_read_decodes_only_requested_value(self):
        value = types.SimpleNamespace(preferences_response=types.SimpleNamespace(results=[
            types.SimpleNamespace(get_preference_result=types.SimpleNamespace(json_value="true"))]))
        get = AsyncMock(return_value=value)
        with patch.object(settings.iterm2, "rpc", types.SimpleNamespace(async_get_preference=get), create=True):
            self.assertTrue(await self.preference(None, "RunJobsInServers"))
        get.assert_awaited_once_with(None, "RunJobsInServers")


if __name__ == "__main__":
    unittest.main()
