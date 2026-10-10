#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Sums up a latency trace of the web app (AC-55): where a key's echo and an opened session
spend their time, by the session's mode, with the slowest keys, round trips and stalls.

    scripts/trace_report.py [trace-….jsonl]    (default: the newest in ~/.iterm-enhancer/logs)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
from fbbridge.common import LOG_DIR as LOGS  # noqa: E402  (FB_APP_DIR moves it, as for the bridge)

# a key's stages in the order it passes them; "net" is the round trip less the bridge's own time
STAGES = [("in", "page: event → send"), ("net", "connection (both ways) and queue"), ("fwd", "bridge: tab forward"),
          ("type", "bridge: hand to iTerm2/tmux"), ("wait", "iTerm2/shell/tmux: until a read shows it"),
          ("read", "bridge: screen read"), ("enc", "bridge: diff and encode"), ("parse", "page: parse"),
          ("draw", "page: apply"), ("paint", "page: next frame")]


def pct(values, p):
    v = sorted(x for x in values if x is not None)
    return v[min(len(v) - 1, int(p / 100 * len(v)))] if v else None


def fmt(x):
    return "–" if x is None else f"{x:.0f}" if x >= 10 else f"{x:.1f}"


def stats(values):
    v = [x for x in values if x is not None]
    return f"n={len(v):<5} p50 {fmt(pct(v, 50)):>6}  p90 {fmt(pct(v, 90)):>6}  max {fmt(max(v) if v else None):>6} ms"


def mode(r):
    return f"{r.get('mode', '?')}/{'remote' if r.get('remote') else 'local'}"


def load(path):
    recs = []
    for line in path.read_text().splitlines():
        try:
            recs.append(json.loads(line))
        except ValueError:
            pass
    return recs


def echo_rows(recs):
    rows = [r for r in recs if r.get("src") == "page" and r.get("ev") == "echo" and r.get("total") is not None]
    for r in rows:
        if r.get("rt") is not None and r.get("bt") is not None:
            r["net"] = max(0.0, r["rt"] - r["bt"])
    return rows


def report(recs, out=print):
    if not recs:
        return out("The trace is empty.")
    span = (recs[-1]["at"] - recs[0]["at"]) / 1000
    builds = {r.get("build") for r in recs if r.get("ev") == "start"}
    out(f"Trace: {len(recs)} records over {span / 60:.1f} min, build {', '.join(sorted(map(str, builds))) or '?'}\n")

    echoes = echo_rows(recs)
    out("== Key → echo on the page (only frames where the cursor moved or its row changed)")
    by_mode = defaultdict(list)
    for r in echoes:
        by_mode[mode(r)].append(r)
    for m, rows in sorted(by_mode.items()):
        out(f"\n{m}: total {stats([r['total'] for r in rows])}")
        for key, label in STAGES:
            out(f"  {label:<44} {stats([r.get(key) for r in rows])}")
        kinds = defaultdict(list)
        for r in rows:
            kinds[r.get("cls", "?")].append(r["total"])
        out("  by key kind: " + "; ".join(f"{k} {stats(v)}" for k, v in sorted(kinds.items())))
        paths = defaultdict(list)
        for r in rows:
            paths[r.get("path", "?")].append(r["total"])
        out("  by input path: " + "; ".join(f"{k} {stats(v)}" for k, v in sorted(paths.items())))
        comp = [r["comp"] for r in rows if r.get("comp") is not None]
        if comp:
            out(f"  composition (held by the keyboard until it ended): {stats(comp)}")
        woke = defaultdict(int)
        for r in rows:
            woke[r.get("woke", "?")] += 1
        out("  the read that showed it was woken by: " + ", ".join(f"{k} {v}" for k, v in sorted(woke.items())))
        out(f"  empty reads before it: {stats([r.get('polls') for r in rows])[:-3]}; other frames before it: "
            f"{sum(1 for r in rows if r.get('skip'))}; WebSocket bytes still buffered at send: max {max((r.get('buf') or 0) for r in rows)}")

    keys = [r for r in recs if r.get("src") == "bridge" and r.get("ev") == "key"]
    by_mode = defaultdict(list)
    for r in keys:
        by_mode[mode(r)].append(r)
    for m, rows in sorted(by_mode.items()):
        out(f"\nbridge's side, {m}: from the key's arrival to its frame {stats([r.get('bt') for r in rows])}")
        for key, label in STAGES[2:7]:
            out(f"  {label:<44} {stats([r.get(key) for r in rows])}")

    noecho = [r for r in recs if r.get("ev") == "noecho" and r.get("src") == "page"]
    if noecho:
        kinds = defaultdict(int)
        for r in noecho:
            kinds[r.get("cls", "?")] += 1
        out(f"\nKeys with no echo within 10 s: {len(noecho)} ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))})")

    out("\n== The slowest 10 keys")
    for r in sorted(echoes, key=lambda r: -r["total"])[:10]:
        parts = " ".join(f"{k}={fmt(r.get(k))}" for k, _ in STAGES if r.get(k) is not None)
        out(f"  {fmt(r['total']):>6} ms  {mode(r):<13} {r.get('cls', '?'):<9} {r.get('path', '?'):<8} {parts} woke={r.get('woke')} polls={r.get('polls')}")

    out("\n== Sessions opened")
    pages = [r for r in recs if r.get("src") == "page" and r.get("ev") == "open"]
    out(f"  page: click → first screen   {stats([r.get('first') for r in pages])}")
    out(f"  page: click → first frame    {stats([r.get('paint') for r in pages])}")
    bridge = [r for r in recs if r.get("src") == "bridge" and r.get("ev") == "open"]
    groups = defaultdict(list)
    for r in bridge:
        groups[mode(r)].append(r.get("ms"))
    for m, v in sorted(groups.items()):
        out(f"  bridge: sub → first screen sent, {m:<13} {stats(v)}")
    hist = [r for r in recs if r.get("ev") == "hist"]
    if hist:
        out(f"  history fetched from iTerm2: {stats([r.get('ms') for r in hist])}, lines up to {max(r.get('lines', 0) for r in hist)}")

    out("\n== Connection and stalls")
    out(f"  ping round trip (page → bridge queue → page)   {stats([r.get('rtt') for r in recs if r.get('ev') == 'ping'])}")
    tmux = [r.get("ms") for r in recs if r.get("ev") == "tmux" and "ms" in r]
    if tmux:
        out(f"  tmux round trip (display -p)                    {stats(tmux)}")
    out(f"  window and title poll (every 2 s)               {stats([r.get('ms') for r in recs if r.get('ev') == 'poll'])}")
    lag = [r.get("ms") for r in recs if r.get("ev") == "lag"]
    out(f"  bridge event loop late by > 20 ms: {len(lag)} times, max {fmt(max(lag) if lag else None)} ms")
    slow = defaultdict(list)
    for r in recs:
        if r.get("ev") == "msg":
            slow[r.get("t")].append(r.get("ms"))
    for t, v in sorted(slow.items()):
        out(f"  bridge handled \"{t}\" in over 20 ms: {stats(v)}")
    tx = defaultdict(list)
    for r in recs:
        if r.get("ev") == "tx":
            tx[r.get("t")].append(r)
    for t, rows in sorted(tx.items()):
        late = sum(1 for r in rows if r.get("ms", 0) > 50)
        out(f"  sent \"{t}\": {stats([r.get('ms') for r in rows])}, bytes p50 {fmt(pct([r.get('bytes') for r in rows], 50))} "
            f"max {max(r.get('bytes', 0) for r in rows)}, over 50 ms {late}")
    jank = [r for r in recs if r.get("ev") == "jank"]
    out(f"  page frames over 100 ms apart: {sum(r.get('count', 0) for r in jank)}, longest {fmt(max((r.get('max', 0) for r in jank), default=None))} ms")
    for what in ("screen", "hist"):
        v = [r.get("ms") for r in recs if r.get("ev") == "draw" and r.get("what") == what]
        if v:
            out(f"  page drawing the {what} over 16 ms: {stats(v)}")


def main(argv):
    path = Path(argv[1]) if len(argv) > 1 else max(LOGS.glob("trace-*.jsonl"), key=lambda p: p.stat().st_mtime, default=None)
    if not path or not path.is_file():
        print(f"No trace file: give one, or record one with ?trace=1 (they go to {LOGS}).", file=sys.stderr)
        return 1
    print(f"{path}\n")
    report(load(path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
