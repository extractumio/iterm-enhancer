#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Remove this project's web-view tool registrations from iTerm2's preferences.

iTerm2 has no API to unregister a tool and rewrites its preferences from memory, so this
only works while iTerm2 is not running. Used by `make uninstall`.

    python3 scripts/clean_registrations.py [--port 47821] [--also <identifier> ...] [--domain D]
"""
import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bridge"))
from fbbridge.registry import DOMAIN, KEY, ours, registrations  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=47821)
p.add_argument("--also", action="append", default=[], help="extra identifier to remove")
p.add_argument("--domain", default=DOMAIN)
a = p.parse_args()

if a.domain == DOMAIN and subprocess.run(["pgrep", "-x", "iTerm2"], capture_output=True).returncode == 0:
    sys.exit("Quit iTerm2 first: it rewrites its preferences from memory and would restore the entries.")
tools = registrations(a.domain)
drop = [i for i, e in tools.items() if ours(e.get("URL"), a.port) or i in a.also]
if not drop:
    print("Nothing to remove.")
    sys.exit(0)
keep = {i: e for i, e in tools.items() if i not in drop}
if keep:
    xml = plistlib.dumps(keep, fmt=plistlib.FMT_XML).decode()
    subprocess.run(["defaults", "write", a.domain, KEY, xml], check=True)
else:
    subprocess.run(["defaults", "delete", a.domain, KEY], check=True)
print("Removed:", ", ".join(drop))
