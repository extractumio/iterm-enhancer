# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Addresses: this Mac's on the network, and the name in a Host header."""
import re
import subprocess


def lan_addresses():
    out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    return [a for a in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out) if not a.startswith("127.")]


def host_name(host):
    """The name in a Host header, without the port."""
    return host.rsplit(":", 1)[0].strip("[]").lower() if host.count(":") <= 1 or host.startswith("[") else host.lower()


def urls(bind, port, addresses):
    """Where the page can be opened, for a server listening on `bind`."""
    hosts = ["127.0.0.1", *addresses] if bind in ("0.0.0.0", "") else [bind]
    return [f"http://{h}:{port}/" for h in hosts]


def firewall_on():
    """True when the macOS application firewall is on: then it must let iTerm2's Python
    accept connections, or other devices wait without an answer."""
    out = subprocess.run(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                         capture_output=True, text=True).stdout
    return "enabled" in out
