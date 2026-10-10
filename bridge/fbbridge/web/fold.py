# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Folds of a pane's history (AC-56): parts the web app shows as one line, which the bridge does
not send until the user asks. Two thirds of a coding agent's history is the diffs of its edits,
the scripts it ran and their output (measured on the owner's panes); a file listing can be
thousands of nearly equal lines. Pure: lines are (text, hard_eol); mirror.py sends the rest."""
import re

CMD_KEEP, CMD_MIN = 2, 4        # a long command: its first lines stay, at least this many more fold
OUT_KEEP, OUT_MIN = 8, 8        # a long tool output: likewise
DIFF_MIN = 6                    # an edit's diff folds from this many lines
SAME_MIN, SAME_HEAD, SAME_TAIL = 20, 3, 2   # nearly equal lines: from 20, the first 3 and last 2 stay
BLOB_MIN = 3                    # rows of encoded data

TOOL = re.compile(r"^⏺ [\w.-]+\(")                 # Claude Code's tool call: "⏺ Bash(…"
RESULT = re.compile(r"^ {2}⎿ ")                     # its result's first line
EDIT = re.compile(r"^ {2}⎿ +(Updated|Added|Removed|Created|Wrote|Deleted)\b")
BLOB = re.compile(r"^[A-Za-z0-9+/=_-]{40,}$")
SHAPE = [(re.compile(r"(?:[~/][\w.@+-]*)+/[\w.@+-]+"), "/p"), (re.compile(r"[A-Za-z0-9+/=_-]{24,}"), "t"),
         (re.compile(r"\d+"), "0"), (re.compile(r"\s+"), " ")]


def text_of(line):
    """A line as encoded by screen.enc_line: its text."""
    return "".join(t if isinstance(t, str) else "".join(t) for t, *_ in line["r"])


def is_blob(text):
    """A row of encoded data (base64, base64url, hex): digits and letters mixed, not a word or a rule."""
    t = text.strip()
    return bool(BLOB.match(t)) and any(c.isdigit() for c in t) and any(c.isalpha() for c in t) and len(set(t)) >= 12


# a line that says something went wrong is never one of many: it stays in sight
NOTEWORTHY = re.compile(r"error|fail|fatal|exception|panic|denied|refused|warn", re.I)


def shape(text):
    mark = "!" + text if NOTEWORTHY.search(text) else ""
    for rx, to in SHAPE:
        text = rx.sub(to, text)
    return text.strip() + mark


def _size(n):
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB"


def folds(rows):
    """Folds of `rows` (a run of a pane's history, oldest first: [(text, hard_eol)]), as
    [{"from", "to", "label"}] with row indexes, inclusive, in order. Rows iTerm wrapped are one
    line. Only a part that is whole is folded: one that may go on after the last row is left for
    when more has come."""
    out = _blobs([t.rstrip() for t, _ in rows])              # rows of data: counted as rows (a long
    spans, cur = [], None                                    # line iTerm wrapped is many); the rest
    for k, (t, eol) in enumerate(rows):                      # by lines: rows joined to their hard end
        cur = [cur[0], k, cur[2] + t] if cur else [k, k, t]
        if eol:
            spans.append(cur)
            cur = None
    if cur:
        spans.append(cur)
    for f in _folds([s[2] for s in spans]):
        a, b = spans[f["from"]][0], spans[f["to"]][1]
        if not any(x["from"] <= b and a <= x["to"] for x in out):
            out.append({"from": a, "to": b, "label": f["label"]})
    return sorted(out, key=lambda f: f["from"])


def _blobs(text):
    """Runs of BLOB_MIN or more rows of encoded data, the next row after them come."""
    out, n, i = [], len(text), 0
    while i < n:
        j = i
        while j < n and is_blob(text[j]):
            j += 1
        if j - i >= BLOB_MIN and j < n:
            data = sum(len(text[k].strip()) for k in range(i, j))
            kind = "hex" if all(re.fullmatch(r"[0-9a-fA-F]+", text[k].strip()) for k in range(i, j)) else "base64"
            out.append({"from": i, "to": j - 1, "label": f"⋯ {_size(data)} of {kind} · {j - i} lines"})
        i = max(j, i + 1)
    return out


def _folds(text):
    text = [t.rstrip() for t in text]
    n = len(text)
    out, taken = [], [False] * n

    def fold(a, b, label):
        if a <= b and not any(taken[a:b + 1]):
            out.append({"from": a, "to": b, "label": label})
            for k in range(a, b + 1):
                taken[k] = True

    def cont(i, indent):
        """Lines after i that go on (indented at least `indent`), and whether something ends them."""
        j = i + 1
        while j < n and text[j].strip() and text[j].startswith(" " * indent) and not RESULT.match(text[j]):
            j += 1
        return j, j < n

    for i, t in enumerate(text):
        if TOOL.match(t):                                    # the command a tool ran, its lines
            j, ends = cont(i, 4)
            if ends and j - (i + 1) >= CMD_KEEP + CMD_MIN:
                k = j - (i + 1) - CMD_KEEP
                fold(i + 1 + CMD_KEEP, j - 1, f"⋯ {k} more lines of the command")
        elif RESULT.match(t):                                # a tool's result: a diff, or output
            j, ends = cont(i, 5)
            k = j - (i + 1)
            if not ends:
                continue
            m = EDIT.match(t)
            if m and k >= DIFF_MIN:
                what = "of the file" if m.group(1) in ("Created", "Wrote") else "of diff"
                fold(i + 1, j - 1, f"⋯ {k} lines {what}")
            elif not m and k >= OUT_KEEP + OUT_MIN:
                fold(i + 1 + OUT_KEEP, j - 1, f"⋯ {k - OUT_KEEP} more lines of output")

    i = 0                                                    # nearly equal lines: a listing, a log
    while i < n:
        s = shape(text[i]) if text[i].strip() and not taken[i] else None
        j = i + 1
        while s and j < n and not taken[j] and text[j].strip() and shape(text[j]) == s:
            j += 1
        if s and j - i >= SAME_MIN and j < n and not text[i].startswith("⏺"):
            fold(i + SAME_HEAD, j - 1 - SAME_TAIL, f"⋯ {j - i - SAME_HEAD - SAME_TAIL} similar lines")
        i = j if s else i + 1
    return sorted(out, key=lambda f: f["from"])


CONTEXT = 1000                  # lines looked at around new ones: a fold may start or end there
SCAN_EVERY = 1.0                # seconds between looks at lines that scrolled into the history


class History:
    """What one browser has of a pane's history, for folding: the text of the lines it was sent
    and the folds it was told of (absolute line numbers)."""

    def __init__(self, keep):
        self.keep = keep            # lines kept (the browser's own limit)
        self.text = {}              # n -> (text, hard_eol)
        self.folds = {}             # first n -> last n of a fold told
        self.pending = False        # lines scrolled off since the last look
        self.scanned = 0.0          # when they were last looked at

    def take(self, first, lines, bulk):
        """Lines first.. as the browser is to get them, and the folds found [{"n", "to", "label"}].
        bulk (opening the pane, older lines): a folded line is None, sent only when asked for.
        Else (lines that scrolled off the screen, which the browser has already): sent as they
        are; their folds come from scan(), at most every SCAN_EVERY seconds."""
        for k, line in enumerate(lines):
            self.text[first + k] = (text_of(line), line["e"])
        if len(self.text) > self.keep + CONTEXT:
            for n in sorted(self.text)[: len(self.text) - self.keep - CONTEXT]:
                del self.text[n]
            low = min(self.text)
            self.folds = {a: b for a, b in self.folds.items() if b >= low}   # folds the browser has no more
        if not bulk:
            self.pending = self.pending or bool(lines)
            return lines, []
        found = self.find(first, first + len(lines) + CONTEXT)      # newer lines known: the end of a fold
        out = list(lines)
        for f in found:
            for n in range(max(f["n"], first), min(f["to"], first + len(lines) - 1) + 1):
                out[n - first] = None
        return out, found

    def due(self, now):
        return self.pending and now - self.scanned >= SCAN_EVERY

    def scan(self, now):
        """Folds among the newest lines (they scrolled off the screen meanwhile)."""
        self.pending, self.scanned = False, now
        if not self.text:
            return []
        hi = max(self.text) + 1
        return self.find(hi - CONTEXT, hi)

    def find(self, lo, hi):
        """New folds among the known lines from lo (a contiguous run of them, up to hi)."""
        ns = []
        for n in range(lo, hi):
            if n in self.text:
                ns.append(n)
            elif ns:
                break
        found = []
        for f in folds([self.text[n] for n in ns]):
            a, b = ns[f["from"]], ns[f["to"]]
            if any(x <= b and a <= y for x, y in self.folds.items()):
                continue                                     # told already (or part of one told)
            self.folds[a] = b
            found.append({"n": a, "to": b, "label": f["label"]})
        return found

    def told(self, a, b):
        """a..b lies in a fold the browser was told of (it may ask only for such lines)."""
        return any(x <= a and b <= y for x, y in self.folds.items())
