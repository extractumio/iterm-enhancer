# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""The bridge's count of its iTerm2 API calls (AC-30): by the task's source, with answer bytes."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge import rpcstats  # noqa: E402


class StatsTest(unittest.TestCase):
    def test_calls_and_bytes_by_source_then_a_new_count(self):
        async def go():
            async def asks(n, size):
                for _ in range(n):
                    rpcstats.count(rpcstats._source.get(), size, "variable_request" if size < 1000 else "get_buffer_request")
            await asyncio.gather(rpcstats.tagged("lifecycle", asks(3, 100)), rpcstats.tagged("web screen", asks(1, 4096)), asks(2, 10))
            await rpcstats.tagged("lifecycle", asks(1, 2048))
        rpcstats.report()
        asyncio.run(go())
        self.assertEqual(rpcstats.report(), "api: 7 calls, 6 KB / min: web screen 1 4KB, lifecycle 4 2KB (get_buffer_request 1 2KB, variable_request 3 0KB), other 2 0KB")
        self.assertIsNone(rpcstats.report())


if __name__ == "__main__":
    unittest.main()
