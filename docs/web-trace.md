# Web app context trace (`?trace=2`)

A trace the user copies from the web app and pastes into a coding agent's chat, so the agent can
find a bug in performance, layout or event handling, reproduce it with the data, and fix it.
Spec: AC-55. Code: `bridge/fbbridge/web/static/tracebuf.js` (the ring and events),
`traceshot.js` (snapshots), `ui/test/tracereplay.mjs` (reading and replaying one).

## Getting one

1. Open the web app with `?trace=2` (for example `https://<mac>.<tailnet>.ts.net/?trace=2`).
2. Use it until the problem happens.
3. Press **trace**, left of ⓘ at the bottom right; it is there on a computer too. The trace is
   copied to the clipboard.
4. Paste it into the agent's chat with a sentence about what went wrong and roughly when.

The page holds the trace in memory only, and a reload starts it anew. It sends nothing: it only
asks the bridge to time each key's stages in the screen messages, which writes no file
(`trace.py` "stages"). Unlike `?trace=1` it **holds what is typed and shown**: the keys sent to
the terminal, the screen's rows, the input box and the history's last rows. What you write in
other fields (the sign-in password, the filter, a session's name, a search, a bookmark's note)
is not kept: their keys show as `·` and the messages carrying them show only their length, as
`"(12)"`. A selection in such a field shows only its length. Error stacks keep file names, not
the page's address. Read the trace before you share it outside your own machine.

## Format (version 1)

The copy is UTF-8 text with at most 1000 lines, each at most 4000 characters (a longer line
is cut and ends in `…`; a named line instead has its longest texts shortened, then its last
fields dropped, and gets `"cut":true`, so it stays JSON), and at most 120,000 characters in all.
When over, the oldest events go first. Typically it is 20–80 KB.

```
#ite-trace 1 {"at":"2026-10-14T17:03:39.029Z","up":1024,"events":19,"dropped":0,"doc":"docs/web-trace.md"}
<t> <event> <json>             ← events, oldest first, with the input box's snapshots among them
|<i> <flags>[ <runs>]|<text>   ← a row of iTerm2's, under the snapshot line before it
:<k> <x>|<text>                ← a line the page laid out, under the snapshot line before it
#state
<t> <name> <json>              ← the page's state when the trace was copied
```

- **Header:** `at` is the copy's wall-clock time and `up` is its `t`. `events` counts the event
  lines kept; `dropped` counts the older ones that left the ring (640 event lines, plus the
  last 3 snapshots of the input box).
- **`t`:** whole milliseconds since the page opened (`performance.now()`). All durations in the
  JSON are in milliseconds.
- **Event lines** are cut at 400 characters. Content kept from keys and texts is cut at 40
  characters; `n` then gives the whole length. Missing fields mean false, none or zero.
- **iTerm2 rows** (`|`):
  - `i` is the row on iTerm2's screen. `-1` is the history's last row, `-2` the one before it,
    and so on.
  - `text` is the row as iTerm2 has it, at iTerm2's width, with trailing spaces removed.
  - `flags` are the page's classes for the row, or `-` for none:

    | Flag | Meaning |
    | --- | --- |
    | `e` | a hard line end |
    | `j` | re-joined with the next row in Wrap |
    | `g` | joined without a space (a word broken at the edge) |
    | `c` | goes on from the row before |
    | `r` | a rule |
    | `s` | a blank row below the text, not shown |
    | `p` | the page drew the cursor here (Claude Code left it below its footer) |
    | `w` | in a widget's region |
    | `h` | not shown at all (for example a rule of the input panel on a phone) |

  - `runs` lists the styled stretches as `start+length*flags`, counted in characters of `text`;
    a run past the text's end is the cursor's cell. The flag values come from `render.js`:

    | Value | Meaning |
    | --- | --- |
    | 1 | bold |
    | 2 | faint (a program's suggestion) |
    | 4 | italic |
    | 8 | underline |
    | 16 | strike |
    | 32 | inverse |
    | 64 | invisible |
    | 256 | the cursor |
    | 4096 | has a background color (added by the trace) |

  - Colors are not kept.
- **Page lines** (`:`): `k` is the line on the page, `x` its left edge in px within the box, and
  `text` the characters as the browser placed them. That includes the space the page draws
  between re-joined rows. `cur` in the `web` line before them is the cursor's `[line, column]`.

### Events

| Event | When | Fields |
| --- | --- | --- |
| `load` | the page started | `build`, `url`, `win` [w, h], `dpr` |
| `key` | a key or text sent to the terminal | `k` its number (sent with it to the bridge); `p` path (`keydown`, `input`, `compose`, `hotkey`, `paste`, `tap`); `d` what was sent (JSON-escaped; a run of one escape sequence as `SEQ×n`); `n`; `in`: from the browser's event to the send |
| `echo` | the next frame after the screen message that showed keys: the keys the bridge names in it (a frame where the cursor moved or its row changed), or, with a bridge that does not time keys, any keys sent before it | `keys` (how many it answered), `k` the first one's number, `rt` from its send to the message, `draw` from the message to the end of drawing, `paint` from there to the next frame, `total` from its event to the paint; `ch` rows changed, `full`, `cur` [row, col] of the cursor in the message; `br`, the bridge's stages for that key (`fwd` bringing its tab forward, `type` handing it to iTerm2 `via` text or tmux keys, `wait` until a screen read showed the change, `polls` reads that found none, `woke` what woke the read, `read` the read, `enc` diff and encoding, `bt` the bridge's whole time from the key's arrival to the frame's send, `skip` frames whose cursor did not move); `net` = `rt - bt`, the connection both ways |

| `noecho` | keys not shown for 5 s (a key that moves nothing, such as ← at a line's start, is never shown) | `keys`, `k` the first one, `ms` |
| `pick` / `open` | a session chosen / its timeline | `sid`; since the pick: `hist` the history message came, `drawn` it was drawn, `scr` the first screen message came, `paint` the first frame, `ready` the first frame with the history drawn and a cursor or an input box; `lines`, `kb` of the history; `box`; `keys` (the keyboard field had the focus); `left` (another session was picked first); `ready: null` (no input within 15 s) |
| `hist` | a history message | `mode` (`reset`, `append`, `prepend`), `first`, `n` lines, `oldest`, `kb` |
| `rx` / `tx` | other messages from / to the bridge | `t` and the small fields; arrays as `[n]`, objects as `{…}`; long strings cut at 60 |
| `ptr` | a pointer lifted | `k` (`tap`, `long` > 500 ms, `drag` > 10 px, `cancel`), `pt` (`mouse`, `touch`, `pen`), `b` button, `x` `y` where it went down, `dx` `dy`, `ms`, `on` the target, `under` what lies under the keyboard field when that was the target, `cell` [row, col] on the screen, `m` modifiers |
| `click` | a click on a button, link, tab, menu item or session | `on`, `label` |
| `kd` | a keydown anywhere | `k` key (`·`: a character in a field other than the terminal's), `c` code (the terminal's field only), `m` (⌘⌃⌥⇧), `r` repeat, `ime`, `on` the target when it is not the terminal's field |
| `inp` / `comp` | `beforeinput` / composition in the terminal's field | `it` inputType, `d`, `ime` / `s` (`start`, `end`), `d` |
| `foc` | the focus moved | `on` |
| `vis` / `show` | the page hidden or shown, or the window focused or blurred / restored | `hidden`, `focus` / `persisted` |
| `vp` | the window or the visual viewport changed (after 250 ms of quiet) | like `size` below |
| `scroll` | the terminal scrolled (after 300 ms of quiet) | `top`, `max`, `bottom` |
| `sel` | the selection changed (after 400 ms) | `n` characters, `d`, `in` |
| `ui` | a notice appeared or changed | `el` (`#toast`, `#status`, `#drops`, `#paused`, `#note`, `#mbwhy`, `#viewnote`), `text` |
| `slow` | drawing a screen or a history message took more than 50 ms | `what`, `ms`, `rows` |
| `long` | a long task (Chromium only) | `ms` |
| `err` | an uncaught error or a rejected promise | `msg`, `at`, `stack`, `promise` |
| `tick` | every 5 s with activity, while shown | `rx` {type: [count, bytes]}, `tx`, `draws`, `dmax`, `jank` [stalls > 100 ms between frames, the longest], `rtt` of the last round trip to the bridge, `dom` rows on the page, `heap` MB (Chromium) |
| `box` + rows | the input box changed (300 ms after its last frame, or every 2 s while frames go on) | `top`, `bottom`, `end` (footer's end), `caret` [row, col], `rows`; then its `\|` rows, a `web` line (`ms`: the time measuring took) and its `:` lines |
| `copy` | the trace was copied | — |

### State lines (after `#state`)

| Line | Fields |
| --- | --- |
| `env` | `ua`, `lang`, `url`, `touch` (hover: none), `fine` (pointer: fine), `build`, `hidden`, `focus` |
| `size` | `win`, `vv` [w, h, offsetTop] of the visual viewport, `dpr`, `term` [w, h] inside, `font` px, `lh` line height, `cell` px, `fits` {cols, rows} at this size, `iterm` [cols, rows], `wrap`, `alt` (a full-screen program), `scroll` [top, height, at the bottom], `mode` shown (`wrap`, `grid`, `fit`) |
| `places` | [x, y, w, h] of the bar, terminal, history, screen, keyboard field, hot keys, buttons, scroll-down button, bookmark line and bar, search, toast, status, session list, file window; `inbox`, `foot`, `cur`; `focus` |
| `sess` | `sid`, `title`, `agent`, `shell`, `state`, `host`, `sessions` count, `appCursor`, `bracketed` |
| `hist` | lines on the page, `first`, `last`, `top` (the screen's first line), `oldest`, `truncated`, `loading`, `dom` rows, `blocks`, `widgets`, `folds`, `open` folds |
| `marks` | `all`, `here`, `shown`; `items` of this session {id, n, len, text}; `line` the wavy line's rect; `marked` {len, head, tail} of the text it marks |
| `box` + rows, `web` + lines | the input box now, as in the events |
| `tail` + rows | the history's last 12 rows |
| `screen` + rows | `cols`, `rows`, `top`, `alt`, `box`; then every screen row (at most 120) |

## Analysing a trace (for the agent)

Save the pasted text to a file in a scratch folder, then sum it up:

```
node ui/test/tracereplay.mjs <file>
```

This prints the event counts, the echo's p50, p90 and max by stage, the five slowest echoes,
sessions opened, round trips, and every `noecho`, `slow`, `long`, `err` and `ui` line. Then
read the events around the time the user names; `t` is shared by every line.

- **Errors first.** An `err` line names the file, line and stack; a `ui` line shows what the
  user was told.
- **Typing is slow.** Compare an `echo`'s stages:
  - a large `br.bt` points to the bridge, iTerm2 or tmux: `wait` and `polls` mean iTerm2 or tmux
    was slow to show the key, `type` is iTerm2 or tmux taking it, `read` and `enc` are the
    bridge's own work (for its event loop and sends, use `?trace=1` and
    `scripts/trace_report.py`);
  - a large `net` (or a large `rt` with no `br`, together with a large `tick.rtt`) points to the
    connection;
  - a large `draw` or `paint`, `slow`, `long` or `jank` points to the page. Look at
    `hist.dom` and `tick.dom` (rows on the page), `heap`, and how many rows the message
    changed (`ch`, `full`).
  - Without `br`, a screen message from output (a spinner) can answer a key early: check that
    `cur` moved.
- **A session opens slowly.** In `open`, `hist` is the bridge's answer; `drawn - hist` is the
  page drawing the history (see `slow` with `what: "hist"`); `scr - hist` is the wait for the
  screen; `paint - scr` is drawing it; `ready` is when input could begin.
- **Layout (Wrap, the input box, the cursor).** In a `box` snapshot, compare iTerm2's rows (`|`)
  with the page's lines (`:`). The text must be the same, re-flowed to the page's width.
  - A missing or extra space at a join: look at the flags `j`, `g` and `c` and at `term.js`
    `wrappedHere`, `inputWraps` and `glued`.
  - The cursor in the wrong place: compare the `256` run with `web.cur`, and `box.caret`.
  - Rows hidden on a phone show `h`.
  - Read the sizes in `size`: `iterm` cols against `fits` cols, `font`, `cell`, `mode`. Read
    the places in `places`, and keep the snapshots in time order to see what changed when.
- **Taps, clicks and keys.** Follow `ptr` → `key` (`p: "tap"`: the arrows a tap sent) → `echo`.
  On a touch screen the keyboard field covers the input, so `on` is `#kbd` and `under` names
  what lies beneath. Follow `kd`, `inp` and `comp` for the keyboard's own events (iOS
  composes predictive text; `ime`), and `foc` and `vis` for where the keys went.
- **Bookmarks and search.** Look at `tx` `locate` and `rx` `located` (`n`, `start`, `size`),
  and at `marks.line` against the rows it covers.

## Reproducing

Write a check like the `ui/test/e2e_web_*.mjs` ones: a real browser on the page's real files
against a fake bridge WebSocket (`open()` in `e2e_web_widgets.mjs` or `e2e_web_trace.mjs`).
`ui/test/tracereplay.mjs` turns the trace into its inputs:

```js
import { device, histLines, parseTrace, screenMessage } from "./tracereplay.mjs";
const trace = parseTrace(fs.readFileSync(file, "utf8"));
const page = await browser.newPage(device(trace));        // the viewport, scale and touch it came from
say(screenMessage(trace));                                 // the screen's rows as the bridge sent them
// the history: say({ t: "hist", mode: "reset", sid: "S1", first: 0, oldest: 0, truncated: false, lines: histLines(trace) })
// a box snapshot's rows: rowData(row) for each row of parseTrace(...).events.find((e) => e.ev === "box")
```

Set the text size (`size.font`), the mode (`size.mode`) and the session's kind (`sess.agent`)
as the trace has them. Then replay the user's steps from the events (`key`, `ptr`, `kd`) and
compare what the page draws with the `:` lines. A check that fails without the fix and passes
with it goes into `e2e_panel.mjs` (see `docs/specs/iterm-enhancer.md`, Definition of Done).
Fixtures keep no personal text: replace the user's words with your own of the same shape.

**Limits.** Of the bridge, only each key's stages come in the trace; its event loop, sends,
polls and tmux round trips come only with `?trace=1`. Colors are not kept. Wide characters are counted as characters, not cells. Only
the last 640 events stay, and only the last 3 snapshots of the input box.
