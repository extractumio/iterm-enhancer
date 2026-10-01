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


def status():
    h = live.health()
    print(f"running:  {h.get('build') if h else 'no (iTerm2 not running, or its Python API is off)'}"
          + (f", bridge {'connected' if h.get('bridge_connected') else 'not connected'}" if h else ""))
    print(f"current:  {install.target('current') or '-'}")
    print(f"previous: {install.target('previous') or '-'}")
    hosts_list()


def fetch(url, dest):
    with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)


def extract(tar, dest):
    """Unpack a package; no member may leave `dest` (the data filter where Python has it)."""
    with tarfile.open(tar) as t:
        if hasattr(tarfile, "data_filter"):
            return t.extractall(dest, filter="data")
        for m in t.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() and Path(m.linkname).is_absolute():
                sys.exit(f"refusing {m.name}: outside the package")
        t.extractall(dest)


def upgrade(tag=None):
    """Download a release's package and SHA256SUMS, check the checksum, install it."""
    base = f"{RELEASES}/download/{tag}" if tag else f"{RELEASES}/latest/download"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        try:
            fetch(f"{base}/{TARBALL}", tmp / TARBALL)
            fetch(f"{base}/SHA256SUMS", tmp / "SHA256SUMS")
        except OSError as e:
            sys.exit(f"download failed ({base}): {e}")
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
    e.add_argument("target", nargs=argparse.REMAINDER, help="as you would give them to ssh, e.g. ai4 or -p 2222 alex@10.0.0.5")
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
