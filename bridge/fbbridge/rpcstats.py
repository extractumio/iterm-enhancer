# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""How much the bridge asks of iTerm2's API, by source (AC-30): iTerm2 answers on its main
thread and writes every answer out as text for its Script Console, so the bridge's calls and
the size of their answers are iTerm2's load. A line a minute in bridge.log:
"api: 1830 calls, 410 KB / min: lifecycle 1100 30KB, web screen 240 300KB, ..."."""
import asyncio
import contextvars

import iterm2

from .common import log

EVERY = 60.0
_source = contextvars.ContextVar("rpc_source", default="other")
_counts = {}            # source -> [calls, answer bytes]
_reporter = None        # the minute task: asyncio keeps only a weak reference to it


def tag(name):
    """Calls from the running task and the tasks it starts count for `name`."""
    _source.set(name)


async def tagged(name, coro):
    tag(name)
    return await coro


def count(source, size, kind=""):
    c = _counts.setdefault(source, [0, 0, {}])
    c[0] += 1
    c[1] += size
    k = c[2].setdefault(kind, [0, 0])
    k[0] += 1
    k[1] += size


def report():
    """The line for the counts so far, which start again; None when nothing was asked."""
    global _counts
    counts, _counts = _counts, {}
    if not counts:
        return None
    calls, size = sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values())

    def kinds(by):        # the requests behind a source, when more than one kind
        top = sorted(((k, v) for k, v in by.items() if k), key=lambda kv: -kv[1][1])[:3]
        return f" ({', '.join(f'{k} {c} {b // 1024}KB' for k, (c, b) in top)})" if len(by) > 1 else ""
    parts = ", ".join(f"{s} {c} {b // 1024}KB{kinds(by)}" for s, (c, b, by) in sorted(counts.items(), key=lambda kv: -kv[1][1]))
    return f"api: {calls} calls, {size // 1024} KB / min: {parts}"


def install():
    """Counts every call of the iterm2 package (its one way to iTerm2), and logs a line a minute."""
    rpc = iterm2.rpc
    if getattr(rpc._async_call, "counted", False):
        return
    call = rpc._async_call

    async def counted(connection, request):
        response = await call(connection, request)
        count(_source.get(), response.ByteSize(), request.WhichOneof("submessage") or "")
        return response
    counted.counted = True
    rpc._async_call = counted

    async def every_minute():
        while True:
            await asyncio.sleep(EVERY)
            line = report()
            if line:
                log(line)
    global _reporter
    _reporter = asyncio.get_running_loop().create_task(every_minute(), context=contextvars.Context())
