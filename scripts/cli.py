# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iterm-enhancer: the one command a user needs after the one-line install (AC-40).

    iterm-enhancer status                    what runs, which builds, which hosts
    iterm-enhancer install                   install the package this command came in
    iterm-enhancer upgrade [--to vX.Y.Z]     download a release, check it, install it
    iterm-enhancer rollback                  back to the build before
    iterm-enhancer uninstall                 remove it (keeps your settings)
    iterm-enhancer hosts                     the remote hosts whose files you browse
    iterm-enhancer hosts enable <ssh destination and options>
    iterm-enhancer hosts remove <host>
    iterm-enhancer web                       web access: on or off, and its addresses
    iterm-enhancer web on [--port N] [--host ADDRESS]
                                                serve the sessions and files to a browser
    iterm-enhancer web off
    iterm-enhancer web password              set or change its password (FB_WEB_PASSWORD, or asked)
"""
import argparse
import getpass
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent              # <package or build>/scripts
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "bridge"))

import install  # noqa: E402
import install_launch as live  # noqa: E402

RELEASES = os.environ.get("FB_RELEASE_URL", "https://github.com/extractumio/iterm-enhancer/releases")
TARBALL = "iterm-enhancer-macos.tar.gz"
# the maintainer's release key, as this installed build carries it: an upgrade must be
# signed by it (AC-40); `make signing-key` makes the key and writes this file
SIGNERS = HERE.parent / "release-signers"
NAMESPACE = "iterm-enhancer-release"


def status():
    h = live.health()
    print(f"running:  {h.get('build') if h else 'no (iTerm2 not running, or its Python API is off)'}"
          + (f", bridge {'connected' if h.get('bridge_connected') else 'not connected'}" if h else ""))
    print(f"current:  {install.target('current') or '-'}")
    print(f"previous: {install.target('previous') or '-'}")
    if h and h.get("windows") is not None:
        print(panels_line(h["ws_clients"], h["windows"]))
    hosts_list()
    web_status(h)


def panels_line(panels, windows):
    """Connected panels against iTerm2's windows: every registration of the tool gives each
    window a panel, and iTerm2 keeps the old ones until it quits (AC-41)."""
    line = f"panels:   {panels} connected, {windows} iTerm2 window{'s' * (windows != 1)}"
    if panels > windows:
        line += f" ({panels - windows} more than windows: iTerm2 keeps panels of earlier registrations until it quits)"
    return line


def fetch(url, dest):
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)


def extract(tar, dest):
    """Unpack a package: plain files and folders inside `dest` only, never a link, a device
    or a setuid bit, on every Python (3.9 has no data filter)."""
    with tarfile.open(tar) as t:
        members = t.getmembers()
        for m in members:
            p = Path(m.name)
            if not (m.isfile() or m.isdir()) or p.is_absolute() or ".." in p.parts:
                sys.exit(f"refusing {m.name}: not a plain file or folder inside the package")
            m.mode &= 0o755
        if hasattr(tarfile, "data_filter"):
            return t.extractall(dest, members, filter="data")
        t.extractall(dest, members)


def verify(sums, sig):
    """True if `sums` is signed by the release key this build carries."""
    key = SIGNERS.read_text() if SIGNERS.is_file() else ""
    if "ssh-" not in key:
        sys.exit("this install carries no release key, so it cannot check a release: "
                 "install with the one-line installer (README)")
    with open(sums, "rb") as source:
        r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(SIGNERS), "-I", NAMESPACE, "-n", NAMESPACE, "-s", str(sig)],
                           stdin=source, capture_output=True)
    return r.returncode == 0


def version(text):
    """(0, 15, 0) for "v0.15.0"; None for a build of a checkout."""
    m = re.fullmatch(r"v(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def check_release(sums, tag):
    """The signed SHA256SUMS names its release: the one asked for, and no older one than this
    build unless asked for by name (a replayed old release, AC-40)."""
    named = next((line.split()[2] for line in sums.read_text().splitlines() if line.startswith("# release ")), None)
    if not named:
        sys.exit("the release does not say which version it is: nothing installed")
    if tag and named != tag:
        sys.exit(f"asked for {tag}, the release says {named}: nothing installed")
    here = (HERE.parent / "BUILD").read_text().strip() if (HERE.parent / "BUILD").is_file() else ""
    if not tag and version(named) and version(here) and version(named) < version(here):
        sys.exit(f"the latest release says {named}, older than the installed {here}: nothing installed "
                 f"(to go back on purpose: iterm-enhancer upgrade --to {named})")


def upgrade(tag=None):
    """Download a release's package, SHA256SUMS and its signature; check the signature with
    the installed release key, then the checksum; install it."""
    base = f"{RELEASES}/download/{tag}" if tag else f"{RELEASES}/latest/download"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        try:
            fetch(f"{base}/{TARBALL}", tmp / TARBALL)
            fetch(f"{base}/SHA256SUMS", tmp / "SHA256SUMS")
            fetch(f"{base}/SHA256SUMS.sig", tmp / "SHA256SUMS.sig")
        except OSError as e:
            sys.exit(f"download failed ({base}): {e}")
        if not verify(tmp / "SHA256SUMS", tmp / "SHA256SUMS.sig"):
            sys.exit("the release is not signed by the release key: nothing installed")
        check_release(tmp / "SHA256SUMS", tag)
        want = next((line.split()[0] for line in (tmp / "SHA256SUMS").read_text().splitlines()
                     if line.endswith(f"  {TARBALL}")), None)
        got = hashlib.sha256((tmp / TARBALL).read_bytes()).hexdigest()
        if want != got:
            sys.exit(f"checksum mismatch for {TARBALL}: nothing installed")
        extract(tmp / TARBALL, tmp / "pkg")
        pkg = tmp / "pkg" / "iterm-enhancer"
        r = subprocess.run([str(pkg / "iterm-enhancer"), "install"])
        sys.exit(r.returncode)


def hosts_list():
    from fbbridge import agentctl
    record = agentctl.load()
    if not record:
        print("hosts:    none yet (focus a tmux -CC pane of a host and click Enable in the Files panel)")
    for key, e in sorted(record.items()):
        print(f"host:     {key}  ({e.get('name', '?')}, {e.get('platform', '?')}, user {e.get('user', '?')})")


def hosts_enable(target):
    from fbbridge import agentctl, hosts
    try:
        e = hosts.enable(target)
    except agentctl.AgentError as err:
        sys.exit(str(err))
    print(f"{' '.join(target)} ready ({e['platform']}, host {e['name']}, user {e['user']}): "
          "its tmux -CC panes now show its files")


def hosts_remove(key):
    from fbbridge import agentctl, hosts
    e = agentctl.entry(key)
    if not e:
        sys.exit(f"{key} is not an enabled host (see: iterm-enhancer hosts)")
    try:
        hosts.remove(e["ssh"])
    except agentctl.AgentError as err:
        sys.exit(f"forgot {key}, but could not remove its helper: {err}")
    print(f"removed the helper from {key}")


def web_status(health=None):
    """What the bridge reports (it knows the real addresses and errors); the settings when
    iTerm2 is not running."""
    from fbbridge.web import config, net
    try:
        cfg = config.load()
    except ValueError as e:
        return print(f"web:      {e}")
    reported = (health or live.health() or {}).get("web")
    if not cfg["enabled"]:
        return print("web:      off (iterm-enhancer web on)")
    if not cfg["password"]:
        return print("web:      on, but no password yet (iterm-enhancer web password)")
    if not reported:
        return print(f"web:      on, port {cfg['port']}; served once iTerm2 runs")
    print(f"web:      {'on' if reported['enabled'] else 'not serving'}, port {cfg['port']}"
          + (f" — {reported['error']}" if reported.get("error") else ""))
    for u in reported.get("urls", []):
        print(f"          {u}")
    if reported["enabled"] and cfg["host"] != "127.0.0.1" and net.firewall_on():
        print("          macOS Firewall is on: if other devices get no answer, allow incoming connections for\n"
              "          iTerm2's Python (macOS asks once; else System Settings → Network → Firewall → Options)")


def ask_password():
    pw = os.environ.get("FB_WEB_PASSWORD")
    if pw is None:
        pw = getpass.getpass("Web access password: ")
        if getpass.getpass("Again: ") != pw:
            sys.exit("The two passwords differ; nothing changed.")
    return pw


def web(cmd, port=None, host=None):
    from fbbridge.web import config
    try:
        cfg = config.load()
        changes = {k: v for k, v in (("port", port), ("host", host)) if v}
        if cmd == "password" or (cmd == "on" and not cfg["password"]):
            changes["password"] = config.hash_password(ask_password())
        if cmd in ("on", "off"):
            changes["enabled"] = cmd == "on"
        cfg = config.update(**changes)
    except ValueError as e:
        sys.exit(f"Not changed: {e}")
    if cmd == "off":
        return print("web:      off; the bridge closes it within a few seconds")
    if cmd == "password":
        print("web:      password changed; browsers signed in before must sign in again")
    if cfg["enabled"]:
        print("web:      on; the bridge serves it within a few seconds" + ("" if cfg["host"] == "127.0.0.1" else
              ".\n          Plain HTTP: on a network you do not trust, use HTTPS instead: tailscale serve --bg "
              + str(cfg["port"])))


def main(argv=None):
    p = argparse.ArgumentParser(prog="iterm-enhancer", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    sub = p.add_subparsers(dest="cmd")
    for name in ("status", "install", "rollback", "uninstall"):
        sub.add_parser(name)
    u = sub.add_parser("upgrade")
    u.add_argument("--to", help="a release tag, e.g. v0.13.0 (default: the latest)")
    h = sub.add_parser("hosts")
    hs = h.add_subparsers(dest="hcmd")
    e = hs.add_parser("enable")
    e.add_argument("target", nargs=argparse.REMAINDER, help="as you would give them to ssh, e.g. devbox.example or -p 2222 alex@devbox.example")
    r = hs.add_parser("remove")
    r.add_argument("host")
    w = sub.add_parser("web")
    ws = w.add_subparsers(dest="wcmd")
    on = ws.add_parser("on")
    on.add_argument("--port", type=int, help="default 8765")
    on.add_argument("--host", help="the address to listen on (default 0.0.0.0: every network of this Mac)")
    ws.add_parser("off")
    ws.add_parser("password")
    a = p.parse_args(argv)
    if a.cmd in (None, "status"):
        return status()
    if a.cmd == "upgrade":
        return upgrade(a.to)
    if a.cmd == "hosts":
        if a.hcmd == "enable":
            if not a.target:
                sys.exit("usage: iterm-enhancer hosts enable <ssh destination and options>")
            return hosts_enable(a.target)
        if a.hcmd == "remove":
            return hosts_remove(a.host)
        return hosts_list()
    if a.cmd == "web":
        if a.wcmd is None:
            return web_status()
        return web(a.wcmd, getattr(a, "port", None), getattr(a, "host", None))
    return install.main([a.cmd] + (["--from", str(HERE.parent)] if a.cmd == "install" else []))


if __name__ == "__main__":
    main()
