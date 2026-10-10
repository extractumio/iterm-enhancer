# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The session list's poll and typing (AC-55's finding): the poll's requests go to iTerm2 a few
at a time, and the poll waits while the user types, but not for ever."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge.web import hub, layout  # noqa: E402


class Browser:
    paused, typed_at = False, -100.0


class PollTest(unittest.TestCase):
    def test_the_polls_requests_go_a_few_at_a_time(self):
        async def go():
            busy = most = 0

            async def ask():
                nonlocal busy, most
                busy += 1
                most = max(most, busy)
                await asyncio.sleep(0.01)
                busy -= 1
                return 1
            got = await asyncio.gather(*(layout.gated(ask()) for _ in range(30)))
            return got, most
        got, most = asyncio.run(go())
        self.assertEqual((sum(got), most), (30, layout.IN_FLIGHT))

    def test_the_poll_waits_while_a_key_came_lately_but_not_for_ever(self):
        async def go():
            loop = asyncio.get_running_loop()
            h = hub.Hub(SimpleNamespace())
            client = Browser()
            h.clients.add(client)
            polls = []

            async def refresh():
                polls.append(loop.time())
            h.refresh = refresh
            with mock.patch.object(hub, "LAYOUT_EVERY", 0.05), mock.patch.object(hub, "TYPING_QUIET", 0.2), \
                    mock.patch.object(hub, "TYPING_WAIT", 0.6):
                task = asyncio.create_task(h.run())
                await asyncio.sleep(0.12)
                quiet = len(polls)
                start = loop.time()
                while loop.time() - start < 1.0:      # typing for a second, a key every 50 ms
                    client.typed_at = loop.time()
                    await asyncio.sleep(0.05)
                typing = [t for t in polls if t > start]
                task.cancel()
            return quiet, typing, start
        quiet, typing, start = asyncio.run(go())
        self.assertGreaterEqual(quiet, 2, "polls when nobody types")
        self.assertTrue(typing, "a poll still comes while typing goes on")
        self.assertGreater(typing[0] - start, 0.4, "but only after the wait")
        self.assertLessEqual(len(typing), 2)


if __name__ == "__main__":
    unittest.main()
