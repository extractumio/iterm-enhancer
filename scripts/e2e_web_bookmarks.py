#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-57 end-to-end: a web bookmark is found again in real iTerm2 after its history is wrapped
anew (a narrower pane numbers its lines anew), and finding it is quick.

Opens its own iTerm2 window, prints numbered lines in it with a text to find among them and a
long line iTerm2 wraps, reads the history as the bridge does (bookmarks.history_rows), finds the
text, narrows the pane, and finds it again where it is now. Types only into its own window.
"""
import asyncio
import sys
import time
from pathlib import Path

import iterm2

from e2e_common import new_window

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from fbbridge.web.bookmarks import find, history_rows, loose  # noqa: E402

TEXT = "the retry loop never resets its counter"


async def main(conn):
    app = await iterm2.async_get_app(conn)
    created = await new_window(conn)
    await asyncio.sleep(1.0)
    win = app.get_window_by_id(created.window_id)
    s = win.current_tab.current_session
    ok = True
    try:
        await s.async_set_grid_size(iterm2.util.Size(100, 24))
        await asyncio.sleep(0.5)
        long = "W" * 120                       # wrapped: 2 rows at 100 columns, 4 at 40 (within iTerm2's 1000)
        # the text only in the output: the command line holds it in two parts
        script = (f"a='the retry loop'; b='never resets its counter'; for i in $(seq 1 120); do echo \"line $i {long}\"; "
                  f"[ $i = 60 ] && echo \"note: $a $b\"; done; echo end\r")
        await s.async_send_text(script, suppress_broadcast=True)
        for _ in range(60):
            await asyncio.sleep(0.5)
            rows = await history_rows(s, 10000)
            if any(t.startswith("end") for _, t in rows[-30:]):
                break
        found = find(rows, TEXT, 0)
        at_wide = found and found[0]
        passed = bool(found) and TEXT[:20] in dict(rows)[at_wide] and found[2] == len(loose(TEXT))
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-57 the text is found in the history at 100 columns → line {at_wide}")

        await s.async_set_grid_size(iterm2.util.Size(40, 24))
        await asyncio.sleep(1.0)
        t = time.perf_counter()
        rows = await history_rows(s, 10000)
        found = find(rows, TEXT, at_wide)
        took = time.perf_counter() - t
        text = dict(rows).get(found[0], "") if found else ""
        passed = bool(found) and found[0] != at_wide and "theretry" in loose(text)
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-57 at 40 columns iTerm2 numbered it anew ({at_wide} → {found and found[0]}) and it is found there: {text!r}")
        passed = took < 2.0
        ok &= passed
        print(f"{'PASS' if passed else 'FAIL'} AC-57 reading {len(rows)} lines and finding it took {took * 1000:.0f} ms")
    finally:
        await asyncio.sleep(0.3)
        await win.async_close(force=True)
    print("PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)


iterm2.run_until_complete(main)
