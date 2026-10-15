# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""What a coding agent in a pane is doing, for the session list's status icon (AC-52):
working, waiting for the user, failed, or done. Claude Code puts a spinner in front of its
terminal title while it works and ✳ when it is idle (measured: ◐ ◑ ◒ ◓ turning, ✳ at rest);
a question or a failure shows only on its screen, so the last rows are read for those."""
import re
import time

SPINNER = re.compile("^[◐-◓✢✶✻✽·⠀-⣿]")   # ◐◑◒◓ ✢✶✻✽ · braille
IDLE = re.compile("^✳")                                                            # ✳
WAITING = re.compile(r"Do you want to|Would you like to|Esc to cancel|\(y/n\)|\[y/N\]|Allow (command|edit|this)|Press Enter to", re.I)
FAILED = re.compile(r"API Error|overloaded_error|Request timed out|Credit balance is too low|usage limit reached|Claude Code crashed", re.I)
# Working, from the screen (a pane whose title the user set has no marks): Claude Code's status
# line while it works, "✻ Combobulating… (6m 8s · ↓ 31.5k tokens)" (measured; done it reads
# "✻ Worked for 2m 12s · done"), or the "esc to interrupt" older builds and Codex show.
WORKING = re.compile("(^|\n)\\s*[\u2722-\u273d\u00b7*]\\s+[^\n]{1,80}\u2026\\s*\\(|esc to interrupt", re.I)
ROWS = 25
ASK_ROWS = 12             # a question is near the bottom: older text above it is history


def claude_marked(title):
    """Claude Code's own marks in front of a title (a spinner or ✳)."""
    return bool(SPINNER.match(title or "") or IDLE.match(title or ""))


def state_of(title, rows):
    """"working", "waiting", "failed" or "done" from the raw title and the screen's last rows."""
    rows = [r.replace("\0", " ") for r in rows]      # cells a program skipped read as NUL
    tail = "\n".join(rows[-ROWS:])
    if WAITING.search("\n".join(rows[-ASK_ROWS:])):
        return "waiting"
    working = bool(SPINNER.match(title or "")) or bool(WORKING.search(tail))
    if not working and FAILED.search(tail):
        return "failed"
    return "working" if working else "done"


STATE_EVERY = 6.0         # seconds a state stands while the title stays the same
_known = {}               # session id -> (title, when read, state)


async def session_state(session, title, now=None):
    """The state, from the screen read again only when the title changed (Claude Code turns its
    spinner while it works) or STATE_EVERY passed: the screen is iTerm2's largest answer (about
    24 KB, its styles included), and the list asks every 2 s for every agent's pane."""
    now = time.monotonic() if now is None else now
    sid = session.session_id
    last = _known.get(sid)
    if last and last[0] == title and now - last[1] < STATE_EVERY:
        return last[2]
    screen = await session.async_get_screen_contents()
    n = screen.number_of_lines
    state = state_of(title, [screen.line(i).string for i in range(max(0, n - ROWS), n)])
    if len(_known) > 500:     # sessions long closed
        _known.clear()
    _known[sid] = (title, now, state)
    return state
