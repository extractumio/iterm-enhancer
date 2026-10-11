# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Search of the shown pane's history in the web app (AC-58): the text the user typed, in every
line the page could load (the history it may load and the screen), not only the part loaded.
Rows iTerm wrapped are one line; a match is told by the row it starts on and how many matches
on that row come before it, as the page finds it among the row's text."""
import bisect

from ..common import log
from .bookmarks import history_rows, later

MAX_HITS = 2000            # matches told at most (the count says when there are more)
MAX_QUERY = 200            # characters of a search


def find(rows, query):
    """[(row, k)] for each match of query (case-insensitive) in rows [(n, text, hard_eol)]: the
    row it starts on and the matches before it on that row; and the whole count. A row's spaces
    at its end are left out, as the page draws it; rows iTerm wrapped are one line."""
    q = query.lower()
    hits, total, parts, starts, size = [], 0, [], [], 0
    per_row = {}

    def scan():
        nonlocal total
        low = "".join(parts).lower()                           # joined once: a line can be thousands of rows
        i = low.find(q)
        firsts = [at for _, at in starts]
        while i >= 0:
            if len(hits) >= MAX_HITS:                          # past the cap only the count goes on ("2000+")
                total += low.count(q, i)
                return
            total += 1
            n = starts[bisect.bisect_right(firsts, i) - 1][0]
            hits.append((n, per_row.get(n, 0)))
            per_row[n] = per_row.get(n, 0) + 1
            i = low.find(q, i + 1)
    for n, text, eol in rows:
        starts.append((n, size))
        parts.append(text.rstrip())
        size += len(parts[-1])
        if eol:
            scan()
            parts, starts, size = [], [], 0
    if starts:
        scan()
    return hits, total


async def search(client, msg):
    """{t: found, ref, sid, q, hits: [[n, k]], total} or {.., error} for {t: find, ref, q}."""
    ref, q, session = str(msg.get("ref", ""))[:40], msg.get("q"), client.session
    if not isinstance(q, str) or not q.strip() or len(q) > MAX_QUERY:
        return await client.send({"t": "found", "ref": ref, "error": f"Type 1 to {MAX_QUERY} characters to find."})
    if not session:
        return await client.send({"t": "found", "ref": ref, "error": "No session is shown."})
    try:
        hits, total = find(await history_rows(session, client.max_history, eol=True), q)
    except Exception as e:                         # the page waits for an answer: it says why
        log(f"web: search: {e!r}")
        return await client.send({"t": "found", "ref": ref, "error": f"The history could not be read: {e}"})
    await client.send({"t": "found", "ref": ref, "sid": session.session_id, "q": q, "hits": hits, "total": total})


def handle(client, msg):
    later(search(client, msg))                     # reading the history takes a while: typing goes on
