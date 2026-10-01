#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-01 end-to-end check: cd in a pane → fbd publishes the new cwd.

Opens a new iTerm2 window, then for plain bash, local tmux and local tmux -CC runs N
`cd`s and measures the time until fbd's SSE stream reports that cwd. Needs the bridge
running (make install && make restart). Closes everything it opened.

    python3 scripts/e2e_cwd.py [N]
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import iterm2

from e2e_common import PORT, TOKEN

args = [a for a in sys.argv[1:] if not a.startswith("--")]
N = int(args[0]) if args else 10
SKIP_CC = "--no-cc" in sys.argv  # local tmux -CC can be blocked by an iTerm2 "Cannot Attach" alert
ROOT = Path(tempfile.mkdtemp(prefix="fb-e2e-")).resolve()
DIRS = [ROOT / f"d{i}" for i in range(N)]
for d in DIRS:
    d.mkdir()

seen = {}  # cwd -> monotonic time first published
last_mode = [None]
lock = threading.Lock()


def sse():
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/events?t={TOKEN}")
    with urllib.request.urlopen(req) as r:
        event = None
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:") and event == "state":
                data = json.loads(line[5:])
                cwd = data.get("cwd")
                if os.environ.get("E2E_DEBUG"):
                    print(f"   [{time.monotonic():.2f}] state mode={data.get('mode')} key={str(data.get('key'))[-12:]} cwd={str(cwd)[-6:]} job={data.get('job')} busy={data.get('busy')} note={data.get('note')}", flush=True)
                last_mode[0] = data.get("mode")
                with lock:
                    seen.setdefault(cwd, time.monotonic())


async def wait_cwd(path, timeout=5.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        with lock:
            if str(path) in seen:
                return seen[str(path)]
        await asyncio.sleep(0.02)
    return None


async def run_mode(name, session):
    lat = []
    for d in DIRS:
        with lock:
            seen.pop(str(d), None)
        t = time.monotonic()
        await session.async_send_text(f"cd {d}\r")
        at = await wait_cwd(d)
        lat.append((at - t) * 1000 if at else None)
        await asyncio.sleep(0.3)
    missed_at = [i for i, x in enumerate(lat) if x is None]
    if missed_at:
        print(f"   {name}: missed cd #{missed_at}", flush=True)
    ok = [x for x in lat if x is not None]
    ok.sort()
    p95 = ok[min(len(ok) - 1, int(len(ok) * 0.95))] if ok else None
    passed = len(ok) == len(lat) and p95 is not None and p95 < 1000
    fmt = lambda v: f"{v:.0f}ms" if v is not None else "-"
    print(f"{name:9s} n={len(lat)} missed={len(lat) - len(ok)} p50={fmt(ok[len(ok)//2] if ok else None)} "
          f"p95={fmt(p95)} {'PASS' if passed else 'FAIL'}", flush=True)
    return passed


async def main(conn):
    threading.Thread(target=sse, daemon=True).start()
    app = await iterm2.async_get_app(conn)
    before = {w.window_id for w in app.terminal_windows}
    await iterm2.Window.async_create(conn)
    await asyncio.sleep(1.5)
    win = [w for w in app.terminal_windows if w.window_id not in before][0]
    await win.async_activate()
    s = win.current_tab.current_session
    results = []
    try:
        results.append(await run_mode("bash", s))
        await s.async_send_text(f"tmux -L fbe2e -f /dev/null new-session -s e2e -c {ROOT}\r")
        for _ in range(50):  # wait until the bridge sees the tmux client
            await asyncio.sleep(0.1)
            if last_mode[0] == "tmux":
                break
        await asyncio.sleep(0.5)
        results.append(await run_mode("tmux", s))
        await s.async_send_text("tmux -L fbe2e kill-server\r")
        await asyncio.sleep(1)
        if SKIP_CC:
            raise StopIteration
        await s.async_send_text(f"tmux -L fbe2e2 -f /dev/null -CC new-session -s e2ecc -c {ROOT}\r")
        pane = None
        for _ in range(40):
            await asyncio.sleep(0.25)
            for c in await iterm2.async_get_tmux_connections(conn):
                if c.owning_session and c.owning_session.session_id == s.session_id:
                    for w in app.terminal_windows:
                        for t in w.tabs:
                            if t.tmux_connection_id == c.connection_id:
                                pane = t.current_session
            if pane:
                break
        if pane:
            await pane.tab.window.async_activate()
            await asyncio.sleep(0.5)
            results.append(await run_mode("tmux -CC", pane))
        else:
            print("tmux -CC  could not attach (iTerm2 tmux integration did not open a window) SKIP")
    except StopIteration:
        print("tmux -CC  skipped (--no-cc)")
    finally:
        os.system("tmux -L fbe2e kill-server 2>/dev/null; tmux -L fbe2e2 kill-server 2>/dev/null")
        await asyncio.sleep(1)
        await win.async_close(force=True)
        for d in DIRS:
            d.rmdir()
        ROOT.rmdir()
    print("PASS" if all(results) else "FAIL")


iterm2.run_until_complete(main)
