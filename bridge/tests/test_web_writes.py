# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Writes to a socket leave nothing behind (measured: an empty part left in a transport's
buffer made the event loop write 0 bytes for as long as the connection lasted, 100% CPU)."""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fbbridge.web.httpd import write_parts  # noqa: E402


async def pending_after(write):
    """The parts left in a connected transport's buffer after `write(writer)` and a drain."""
    held = []

    async def keep(reader, writer):          # the other end reads and keeps the connection
        held.append(writer)
        await reader.read(100)
    server = await asyncio.start_server(keep, "127.0.0.1", 0)
    _, writer = await asyncio.open_connection(*server.sockets[0].getsockname()[:2])
    write(writer)
    await writer.drain()
    await asyncio.sleep(0.05)
    left = len(writer.transport._buffer)
    writer.close()
    for w in held:
        w.close()
    server.close()
    return left


class WriteTest(unittest.TestCase):
    def test_a_request_without_a_body_leaves_no_empty_part(self):
        self.assertEqual(asyncio.run(pending_after(lambda w: write_parts(w, b"GET / HTTP/1.1\r\n\r\n", b""))), 0)
        self.assertEqual(asyncio.run(pending_after(lambda w: write_parts(w, b"head", b"payload"))), 0)


if __name__ == "__main__":
    unittest.main()
