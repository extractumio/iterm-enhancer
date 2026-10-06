# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""iterm-filebrowser: the one command a user needs after the one-line install (AC-40).

    iterm-filebrowser status                    what runs, which builds, which hosts
    iterm-filebrowser install                   install the package this command came in
    iterm-filebrowser upgrade [--to vX.Y.Z]     download a release, check it, install it
    iterm-filebrowser rollback                  back to the build before
    iterm-filebrowser uninstall                 remove it (keeps your settings)
    iterm-filebrowser hosts                     the remote hosts whose files you browse
    iterm-filebrowser hosts enable <ssh destination and options>
    iterm-filebrowser hosts remove <host>
"""
import argparse
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

RELEASES = os.environ.get("FB_RELEASE_URL", "https://github.com/extractumio/iterm-extension/releases")
TARBALL = "iterm-filebrowser-macos.tar.gz"
# the maintainer's release key, as this installed build carries it: an upgrade must be
# signed by it (AC-40); `make signing-key` makes the key and writes this file
SIGNERS = HERE.parent / "release-signers"
NAMESPACE = "iterm-filebrowser-release"


def status():
    h = live.health(install.legacy(install.target("current")))
    print(f"running:  {h.get('build') if h else 'no (iTerm2 not running, or its Python API is off)'}"
          + (f", bridge {'connected' if h.get('bridge_connected') else 'not connected'}" if h else ""))
    print(f"current:  {install.target('current') or '-'}")
    print(f"previous: {install.target('previous') or '-'}")
    if h and h.get("windows") is not None:
        print(panels_line(h["ws_clients"], h["windows"]))
    hosts_list()


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
                 f"(to go back on purpose: iterm-filebrowser upgrade --to {named})")


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
        pkg = tmp / "pkg" / "iterm-filebrowser"
        r = subprocess.run([str(pkg / "iterm-filebrowser"), "install"])
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
        sys.exit(f"{key} is not an enabled host (see: iterm-filebrowser hosts)")
    try:
        hosts.remove(e["ssh"])
    except agentctl.AgentError as err:
        sys.exit(f"forgot {key}, but could not remove its helper: {err}")
    print(f"removed the helper from {key}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="iterm-filebrowser", description=__doc__.split("\n\n")[0],
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
    a = p.parse_args(argv)
    if a.cmd in (None, "status"):
        return status()
    if a.cmd == "upgrade":
        return upgrade(a.to)
    if a.cmd == "hosts":
        if a.hcmd == "enable":
            if not a.target:
                sys.exit("usage: iterm-filebrowser hosts enable <ssh destination and options>")
            return hosts_enable(a.target)
        if a.hcmd == "remove":
            return hosts_remove(a.host)
        return hosts_list()
    return install.main([a.cmd] + (["--from", str(HERE.parent)] if a.cmd == "install" else []))


if __name__ == "__main__":
    main()
