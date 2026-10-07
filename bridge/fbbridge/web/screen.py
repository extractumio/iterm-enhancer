# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Encoding of iTerm2 screen lines and profile colors for the browser."""
from itertools import groupby
from operator import itemgetter

FLAGS = (("bold", 1), ("faint", 2), ("italic", 4), ("underline", 8), ("strikethrough", 16),
         ("inverse", 32), ("invisible", 64), ("blink", 128))
CURSOR = 256
VISIBLE_WHEN_BLANK = 8 | 16 | 32 | CURSOR   # a space with these flags still draws something


def enc_color(c):
    """null = default, "R" = reversed default, int = 256-palette index, "#rrggbb" = RGB."""
    if c is None:
        return None
    if c.is_standard:
        return c.standard
    if c.is_rgb:
        return "#%02x%02x%02x" % (c.rgb.red, c.rgb.green, c.rgb.blue)
    if c.is_alternate and c.alternate.name == "REVERSED_DEFAULT":
        return "R"
    return None


def enc_style(st):
    if st is None:
        return (None, None, 0)
    flags = 0
    for name, bit in FLAGS:
        if getattr(st, name):
            flags |= bit
    return (enc_color(st.fg_color), enc_color(st.bg_color), flags)


def enc_cells(cells):
    """Plain single-cell text as one string; otherwise the cells as a list so the client can
    pin each one to the grid ("" marks the right half of a wide character)."""
    if all(len(c) == 1 and c < "\u2000" for c in cells):
        return "".join(cells)
    return cells


def enc_line(line, cursor_x=None):
    """A line as {"r": runs, "e": hard_eol}; a run is [text-or-cells, fg, bg, flags].

    Trailing blank cells are dropped (they cost bytes and make re-wrapped text break early);
    the cursor cell gets its own run with the CURSOR flag, padded out past the text."""
    cells, styles, x = [], {}, 0       # iTerm2 shares one style object across a run of cells
    while True:
        try:
            ch = line.string_at(x)
        except IndexError:
            break
        st = line.style_at(x)
        key = styles.get(id(st)) or styles.setdefault(id(st), enc_style(st))
        if x == cursor_x:
            key = (key[0], key[1], key[2] | CURSOR)
        # A cell the cursor skipped over (apps like Claude Code draw that way) reads as NUL.
        cells.append((" " if ch == "\x00" else ch, key))
        x += 1
    while cells and cells[-1][0] == " " and cells[-1][1][1] is None and not cells[-1][1][2] & VISIBLE_WHEN_BLANK:
        cells.pop()
    runs = [[enc_cells([c for c, _ in group]), *key] for key, group in groupby(cells, key=itemgetter(1))]
    if cursor_x is not None and cursor_x >= x:       # cursor past the line's cells
        if cursor_x > len(cells):
            runs.append([" " * (cursor_x - len(cells)), None, None, 0])
        runs.append([" ", None, None, CURSOR])
    return {"r": runs, "e": line.hard_eol}


def line_key(line):
    """Cheap identity of a line's cells and styles, to skip re-encoding unchanged lines."""
    proto = getattr(line, "_LineContents__proto", None)
    return proto.SerializeToString() if proto is not None else None
