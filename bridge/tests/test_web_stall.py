# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iTerm2 not answering (AC-55): browsers are told when its answer is late, and when it comes;
nothing is asked while nobody looks; a browser that joins meanwhile is told too."""
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge.web import hub, stall  # noqa: E402


class Browser:
    paused = False

    def __init__(self):
        self.got = []

    async def send_quietly(self, text):
        self.got.append(text)

    async def send(self, msg):
        self.got.append(hub.encode(msg))


def run(delays, looking=True, joiner_at=None):
    """The messages a browser gets while iTerm2 answers each check after delays[i] seconds."""
    async def go():
        h = hub.Hub(SimpleNamespace())
        h.layout = []
        b, late = Browser(), Browser()
        if looking:
            h.clients.add(b)
        asked = []

        async def probe(app):
            asked.append(1)
            await asyncio.sleep(delays[min(len(asked), len(delays)) - 1])
        with mock.patch.object(stall, "EVERY", 0.02), mock.patch.object(stall, "STALL", 0.1), \
                mock.patch.object(stall, "log", lambda *a: None):
            task = asyncio.create_task(stall.watch(h, probe))
            if joiner_at is not None:
                await asyncio.sleep(joiner_at)
                await h.join(late)
            await asyncio.sleep(0.5)
            task.cancel()
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        mine = [m for m in b.got if '"iterm"' in m]
        return mine, len(asked), [m for m in late.got if '"iterm"' in m]
    return asyncio.run(go())


class StallTest(unittest.TestCase):
    def test_a_late_answer_is_told_then_its_end(self):
        got, _, _ = run([0.3, 0.0])
        self.assertEqual(len(got), 2, got)
        self.assertIn('"ok":false', got[0])
        self.assertIn('"ok":true', got[1])
        self.assertGreaterEqual(int(got[1].split('"ms":')[1].rstrip("}")), 250)

    def test_quick_answers_say_nothing(self):
        got, asked, _ = run([0.0])
        self.assertEqual(got, [])
        self.assertGreater(asked, 3)

    def test_nothing_is_asked_while_nobody_looks(self):
        _, asked, _ = run([0.0], looking=False)
        self.assertEqual(asked, 0)

    def test_a_browser_joining_meanwhile_is_told(self):
        _, _, late = run([0.4, 0.0], joiner_at=0.2)
        self.assertIn('"ok":false', late[0])


if __name__ == "__main__":
    unittest.main()
