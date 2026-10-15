# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Lines a full-screen program scrolled off its alternate screen (AC-56), which the page folds.
iTerm2 keeps them in the scrollback when the profile says so ("Save lines to scrollback in
alternate screen mode"); its window shows only the screen, the page shows the history above it.
Measured in iTerm2 3.7: while `showingAlternateScreen` is on, the number of the screen's first
line (the scrollback's height plus its overflow) grows by exactly the lines the program scrolls
off, so a span runs from that number when the flag went on to its number last seen while the
flag was on: at the "off" notice the number may already count the shell's lines after the
program (the restored screen scrolls as it prints), which are the user's. The program's last
screen is dropped on exit. mc scrolls nothing off; a coding agent's full-screen mode redraws
and scrolls all the time. A clear (⌘K, /clear) starts the numbers over, and a change of the
pane's width wraps the history anew (bookmarks.py): both drop the session's spans, as hiding
the wrong lines is worse than showing the program's. Pure: the hub feeds it (its notices open
and close spans, its poll and the mirror's frames tell the number as it grows)."""

MOST = 200          # spans kept per session: the oldest go


class Spans:
    def __init__(self):
        self.spans = {}      # session id -> [[first line, line after the last]]; None while it runs
        self.seen = {}       # session id -> (the screen's first line, the pane's width) last seen

    def running(self, sid):
        spans = self.spans.get(sid)
        return bool(spans) and spans[-1][1] is None

    def _note(self, sid, top, width):
        last = self.seen.get(sid)
        if last and (top < last[0] or width != last[1]):      # cleared, or wrapped anew
            self.clear(sid)
        self.seen[sid] = (top, width)

    def open(self, sid, top, width):
        """The flag went on (or was on when the bridge started), the screen's first line there."""
        self._note(sid, top, width)
        if not self.running(sid):
            spans = self.spans.setdefault(sid, [])
            spans.append([top, None])
            del spans[:-MOST]

    def grew(self, sid, top, width):
        """The screen's first line as seen now (a frame, a poll), flag on or off."""
        self._note(sid, top, width)

    def close(self, sid):
        """The flag went off: the span ends where the number was last seen while it was on."""
        if not self.running(sid):
            return
        spans, top = self.spans[sid], self.seen[sid][0]
        if top > spans[-1][0]:
            spans[-1][1] = top
        else:
            spans.pop()              # nothing scrolled off (less, man, a short look at mc)

    def clear(self, sid):
        self.spans.pop(sid, None)
        self.seen.pop(sid, None)

    def keep(self, sids):
        """Only these sessions (the others closed)."""
        for sid in set(self.spans) | set(self.seen):
            if sid not in sids:
                self.clear(sid)

    def ranges(self, sid, lo, hi):
        """[(a, b)]: the lines a..b among lo..hi-1 a full-screen program scrolled off, in order."""
        out = []
        for a, b in self.spans.get(sid, ()):
            x, y = max(a, lo), min(hi if b is None else b, hi) - 1
            if x <= y:
                out.append((x, y))
        return out
