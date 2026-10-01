#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Make the maintainer's release key once (AC-40): an ed25519 ssh key outside the
repository (FB_RELEASE_KEY, default ~/.config/iterm-filebrowser/release-key; ssh-keygen
asks for a passphrase), and its public half in release-signers and install.sh, which every
package carries. Run it in your own terminal, then commit the two files.

    python3 scripts/signing_key.py
"""
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAMESPACE = "iterm-filebrowser-release"
KEY = Path(os.environ.get("FB_RELEASE_KEY") or Path.home() / ".config/iterm-filebrowser/release-key")


def signers_line(pub):
    return f'{NAMESPACE} namespaces="{NAMESPACE}" {" ".join(pub.split()[:2])}'


def write(pub):
    """The public key into release-signers and install.sh (they must say the same)."""
    line = signers_line(pub)
    signers = REPO / "release-signers"
    head = "".join(l for l in signers.read_text().splitlines(keepends=True) if l.startswith("#"))
    signers.write_text(head + line + "\n")
    sh = REPO / "scripts/install.sh"
    sh.write_text(re.sub(r"(?m)^SIGNERS='.*'$", f"SIGNERS='{line}'", sh.read_text()))


def main():
    if KEY.exists():
        sys.exit(f"{KEY} exists: not replaced (a new key would refuse every install that trusts the old one)")
    KEY.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-C", NAMESPACE, "-f", str(KEY)], check=True)
    write(KEY.with_suffix(".pub").read_text())
    print(f"Key: {KEY} (keep it and its passphrase safe; back it up)\n"
          "Public half written to release-signers and scripts/install.sh: commit both.")


if __name__ == "__main__":
    main()
