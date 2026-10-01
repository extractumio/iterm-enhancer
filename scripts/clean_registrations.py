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

p = argparse.ArgumentParser()
p.add_argument("--port", type=int, default=47821)
p.add_argument("--also", action="append", default=[], help="extra identifier to remove")
p.add_argument("--domain", default="com.googlecode.iterm2")
a = p.parse_args()

if a.domain == "com.googlecode.iterm2" and subprocess.run(["pgrep", "-x", "iTerm2"], capture_output=True).returncode == 0:
    sys.exit("Quit iTerm2 first: it rewrites its preferences from memory and would restore the entries.")
out = subprocess.run(["defaults", "export", a.domain, "-"], capture_output=True).stdout
tools = plistlib.loads(out).get("NoSyncDynamicTools", {}) if out else {}
drop = [i for i, e in tools.items() if f"://127.0.0.1:{a.port}/" in (e.get("URL") or "") or i in a.also]
if not drop:
    print("Nothing to remove.")
    sys.exit(0)
keep = {i: e for i, e in tools.items() if i not in drop}
if keep:
    xml = plistlib.dumps(keep, fmt=plistlib.FMT_XML).decode()
    subprocess.run(["defaults", "write", a.domain, "NoSyncDynamicTools", xml], check=True)
else:
    subprocess.run(["defaults", "delete", a.domain, "NoSyncDynamicTools"], check=True)
print("Removed:", ", ".join(drop))
