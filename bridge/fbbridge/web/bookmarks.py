# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Bookmarks of the web app's terminal (AC-57): text a user selected, with a note, kept on the
Mac for every signed-in browser (web-bookmarks.json, 0600). A bookmark is found again by its
text, nearest the line it was on: iTerm2 numbers lines anew when it wraps them again (a width
change), when tmux -CC attaches again and when the scrollback is cleared."""
import asyncio
import json
import re
import secrets
import time


from .. import setup_state
from ..common import log
from ..resolve import resolve

MAX_ITEMS = 300
MAX_TEXT = 2000          # characters of selected text kept (a longer one is cut)
MAX_NOTE = 1000
PRINT = 200              # letters and digits of the text it is looked for by
PARTS = (48, 20)         # then by parts of it this long, in turn
CHUNK = 500              # lines per async_get_contents call
NEAR = 1000              # lines either side of a bookmark's line read first
GONE = "These lines are no longer in this session's history."
MESSAGES = ("mark", "unmark", "locate")
REFS = 100               # saves remembered by their ref
NOT_ALNUM = re.compile(r"[\W_]+")


class MarkError(Exception):
    """Why a bookmark was not saved; the page shows it."""


def loose(text):
    """Only letters and digits, lowercase: the same in a wrapped row, a re-joined line and
    rendered Markdown. marktext.js has the same rule (bridge/tests/loose_cases.json)."""
    return NOT_ALNUM.sub("", text.lower())        # \w is str.isalnum() and "_" (re's documentation)


def find(rows, text, hint):
    """(line, start, size) for the text in rows [(line, row text)]: the line where its letters
    and digits [start:start+size] begin, the place nearest `hint` when they are there more than
    once; None when they are not there. Rows are joined without breaks, so wrapping does not
    matter. The whole print wins wherever it is; else the part found nearest `hint`, as a
    selection in rendered Markdown lacks what the source has (a link's address, a table's bars)."""
    whole = loose(text)[:PRINT]
    if not whole:
        return (hint, 0, 0) if any(n == hint for n, _ in rows) else None
    starts, parts, at = [], [], 0
    for _, t in rows:
        starts.append(at)
        parts.append(s := loose(t))
        at += len(s)
    joined = "".join(parts)
    k = next((i for i, (n, _) in enumerate(rows) if n >= hint), len(rows) - 1)   # the hint's row (or the last)
    at_hint = starts[k] if rows else 0
    hit = _nearest(joined, whole, at_hint)
    if hit is not None:
        return rows[_row_at(starts, hit)][0], 0, len(whole)
    best = None
    for size in (k for k in PARTS if k < len(whole)):
        for start in dict.fromkeys([*range(0, len(whole) - size + 1, size), len(whole) - size]):
            i = _nearest(joined, whole[start:start + size], at_hint)
            if i is not None and (best is None or abs(i - at_hint) < abs(best[0] - at_hint)):
                best = (i, start, size)
        if best:
            break
    return (rows[_row_at(starts, best[0])][0], best[1], best[2]) if best else None


def _nearest(joined, needle, at):
    """The index of needle nearest `at` in joined: the last one before it and the first one
    from it are the only candidates (two searches, however often it is there)."""
    before, after = joined.rfind(needle, 0, at + len(needle) - 1), joined.find(needle, at)
    if before < 0 and after < 0:
        return None
    if before < 0 or (after >= 0 and after - at < at - before):
        return after
    return before


def _row_at(starts, i):
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= i:
            lo = mid
        else:
            hi = mid - 1
    return lo


class Store:
    """The bookmarks, in the order they were made. Writes go one at a time, off the event loop
    (it carries the typing). A file that cannot be read is kept as it is: nothing is saved over it."""

    def __init__(self, path=None):
        self.path, self.broken, self.items = path, None, {}
        self.refs = {}             # the page's ref of a save -> the bookmark it made (a save sent again)
        self.lock = asyncio.Lock()
        if not path:
            return
        try:
            data = json.loads(path.read_text())
            for it in data["items"]:
                self.items[str(it["id"])] = clean_stored(it)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            self.broken = f"{path.name} cannot be read ({type(e).__name__}: {e}); move it away to keep new bookmarks"
            self.items = {}
            log(f"web: bookmarks: {self.broken}")

    def all(self):
        return list(self.items.values())

    async def save(self):
        if self.broken:
            raise MarkError(f"Not saved: {self.broken}.")
        if not self.path:
            return
        data = {"version": 1, "items": self.all()}
        async with self.lock:
            try:
                await asyncio.get_running_loop().run_in_executor(None, setup_state.write, self.path, data)
            except OSError as e:
                raise MarkError(f"Not saved: {e.strerror or e}.") from e

    async def add(self, fields):
        if self.broken:
            raise MarkError(f"Not saved: {self.broken}.")
        if len(self.items) >= MAX_ITEMS:
            raise MarkError(f"{MAX_ITEMS} bookmarks are kept at most: delete some first (Bookmarks, below the sessions).")
        item = {"id": secrets.token_urlsafe(9), "at": int(time.time()), **fields}
        self.items[item["id"]] = item
        try:
            await self.save()
        except MarkError:
            del self.items[item["id"]]
            raise
        return item

    async def change(self, mid, **fields):
        old = self.items.get(mid)
        if not old:
            raise MarkError("This bookmark was deleted meanwhile.")
        self.items[mid] = {**old, **fields}
        try:
            await self.save()
        except MarkError:
            self.items[mid] = old
            raise
        return self.items[mid]

    async def remove(self, mid):
        old = self.items.pop(mid, None)
        if old is None:
            return False
        try:
            await self.save()
        except MarkError:
            self.items[mid] = old
            raise
        return True


def clean_stored(it):
    return {"id": str(it["id"]), "sid": str(it["sid"]), "key": it.get("key") or None, "n": int(it["n"]),
            "text": str(it["text"])[:MAX_TEXT], "note": str(it.get("note", ""))[:MAX_NOTE],
            "title": str(it.get("title", "")), "host": it.get("host") or None, "at": int(it.get("at", 0))}


def note_of(msg):
    note = msg.get("note", "")
    if not isinstance(note, str):
        raise MarkError("The note must be text.")
    if len(note) > MAX_NOTE:
        raise MarkError(f"The note is longer than {MAX_NOTE} characters.")
    return note.strip()


async def tmux_key(conn, session):
    """The tmux pane a tmux -CC session shows (it outlives the iTerm2 session when the
    integration attaches again), else None. Not for plain tmux: its pane is the one the client
    shows now, which may not be the one the history came from."""
    try:
        if await session.async_get_variable("tmuxRole") != "client":
            return None
        key = (await resolve(conn, session)).get("key") or ""
    except Exception as e:      # no key: the bookmark stays with this session only
        log(f"web: bookmarks: tmux pane of {session.session_id}: {e!r}")
        return None
    return key if key.startswith(("tmux:", "tmux-remote:")) else None


def item_of(layout, sid):
    for g in layout or []:
        for it in g["items"]:
            if it["id"] == sid:
                return it
    return None


async def history_rows(session, max_history, around=None, eol=False):
    """[(line, text)] of what the page could load (the history it may load and the screen), or
    only NEAR lines either side of line `around`; eol: [(line, text, hard end)] (a search,
    search.py). Each chunk is numbered from its own start: one that comes back short (iTerm2
    dropped lines meanwhile) shifts no other. A cell the program skipped is a space, as the page
    draws it (screen.py)."""
    info = await session.async_get_line_info()
    top = info.overflow + info.scrollback_buffer_height
    first, end = max(info.overflow, top - max_history), top + info.mutable_area_height
    if around is not None:
        first, end = max(first, around - NEAR), min(end, around + NEAR)
    starts = range(first, end, CHUNK)
    chunks = await asyncio.gather(*(session.async_get_contents(s, min(CHUNK, end - s)) for s in starts))
    if eol:
        return [(s + k, line.string.replace("\x00", " "), line.hard_eol) for s, chunk in zip(starts, chunks) for k, line in enumerate(chunk)]
    return [(s + k, line.string.replace("\x00", " ")) for s, chunk in zip(starts, chunks) for k, line in enumerate(chunk)]


async def handle(client, msg):
    """mark (new: {ref, sid, n, text, note}; changed: {ref, id, note}), unmark {id}, locate {id}."""
    t, hub = msg.get("t"), client.hub
    if t == "unmark":
        if await hub.marks.remove(str(msg.get("id", ""))):
            hub.tell({"t": "unmark", "id": str(msg["id"])})
        return
    if t == "locate":                              # reading the history takes a while: typing goes on
        return later(locate(client, str(msg.get("id", ""))))
    ref = str(msg.get("ref", ""))[:40]
    if ref and ref in hub.marks.refs:              # sent again after no answer came: kept once
        return await client.send({"t": "marked", "ref": ref, "id": hub.marks.refs[ref]})
    try:
        if msg.get("id"):
            item = await hub.marks.change(str(msg["id"]), note=note_of(msg))
        else:
            item = await new_mark(client, msg)
    except MarkError as e:
        return await client.send({"t": "marked", "ref": ref, "error": str(e)})
    if ref:
        hub.marks.refs[ref] = item["id"]
        while len(hub.marks.refs) > REFS:
            del hub.marks.refs[next(iter(hub.marks.refs))]
    await client.send({"t": "marked", "ref": ref, "id": item["id"]})
    hub.tell({"t": "mark", "item": item})


async def new_mark(client, msg):
    session = client.session
    if not session or msg.get("sid") != session.session_id:
        raise MarkError("This session is no longer the one shown: select the text again.")
    if not client.app.get_session_by_id(session.session_id):
        raise MarkError("This session has closed.")
    text, note = msg.get("text"), note_of(msg)
    if not isinstance(text, str) or not text.strip():
        raise MarkError("Select some text first.")
    try:
        n = int(msg.get("n"))
    except (TypeError, ValueError):
        raise MarkError("The selection's line is not known: select the text again.") from None
    shown = item_of(client.hub.layout, session.session_id) or {}
    return await client.hub.marks.add({"sid": session.session_id, "key": await tmux_key(client.conn, session), "n": n,
                                       "text": text[:MAX_TEXT], "note": note, "title": shown.get("title", ""),
                                       "host": shown.get("host")})


async def locate(client, mid):
    """Where a bookmark's text is now in the shown pane: {t: located, id, n, size} or {n: None, why}."""
    item, session = client.hub.marks.items.get(mid), client.session
    if not item:
        return await client.send({"t": "located", "id": mid, "n": None, "why": "This bookmark was deleted meanwhile."})
    if not session or session.session_id != item["sid"]:
        return await client.send({"t": "located", "id": mid, "n": None, "why": "Its session is not the one shown."})
    try:                                           # near its line first: the whole history only if not there
        found = find(await history_rows(session, client.max_history, item["n"]), item["text"], item["n"])
        if not found or found[2] < len(loose(item["text"])[:PRINT]):
            found = find(await history_rows(session, client.max_history), item["text"], item["n"])
    except Exception as e:                         # the page waits for an answer: it says why
        log(f"web: bookmarks: locate: {e!r}")
        return await client.send({"t": "located", "id": mid, "n": None, "why": f"The history could not be read: {e}"})
    if not found:
        return await client.send({"t": "located", "id": mid, "sid": item["sid"], "n": None, "why": GONE})
    n, start, size = found
    await client.send({"t": "located", "id": mid, "sid": item["sid"], "n": n, "start": start, "size": size})
    if n != item["n"]:                             # where to look first next time; the marks move there
        try:
            client.hub.tell({"t": "mark", "item": await client.hub.marks.change(mid, n=n)})
        except MarkError as e:
            log(f"web: bookmarks: {e}")


_tasks = set()


def later(coro):
    """coro on its own task (kept until it ends), so the page's next messages are not held up."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def rebind(client, session):
    """A tmux -CC pane shown again in a new iTerm2 session: the bookmarks of the same tmux pane,
    whose session closed, move to it. Never raises (it runs beside the stream): logs."""
    try:
        await _rebind(client, session)
    except Exception as e:
        log(f"web: bookmarks: rebind: {e!r}")


async def _rebind(client, session):
    marks, hub = client.hub.marks, client.hub
    live = {it["id"] for g in hub.layout or [] for it in g["items"]}
    if not any(it["key"] and it["sid"] not in live for it in marks.all()):
        return
    key = await tmux_key(client.conn, session)
    for it in marks.all():
        if key and it["key"] == key and it["sid"] not in live and it["sid"] != session.session_id:
            try:
                hub.tell({"t": "mark", "item": await marks.change(it["id"], sid=session.session_id)})
            except MarkError as e:                 # not moved: still listed under its closed session
                log(f"web: bookmarks: {e}")
                return

