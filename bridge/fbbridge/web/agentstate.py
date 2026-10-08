# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""What a coding agent in a pane is doing, for the session list's status icon (AC-52):
working, waiting for the user, failed, or done. Claude Code puts a spinner in front of its
terminal title while it works and ✳ when it is idle (measured: ◐ ◑ ◒ ◓ turning, ✳ at rest);
a question or a failure shows only on its screen, so the last rows are read for those."""
import re

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


async def session_state(session, title):
    screen = await session.async_get_screen_contents()
    n = screen.number_of_lines
    return state_of(title, [screen.line(i).string for i in range(max(0, n - ROWS), n)])
