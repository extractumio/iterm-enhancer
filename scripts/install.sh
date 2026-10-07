#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# iterm-enhancer: install or upgrade with one line (AC-40)
#
#   curl -fsSL https://github.com/extractumio/iterm-extension/releases/latest/download/install.sh | sh
#
# Downloads the latest release's package, its SHA256SUMS and their signature into a
# temporary folder, checks the signature with the release key below, then the checksum,
# and runs the package's own installer (a versioned build in
# ~/.iterm-enhancer/builds, the bridge in iTerm2's AutoLaunch, health check,
# rollback when the new build does not come up). Nothing is run before the check passes.
# Everything happens in main, called on the last line: a cut-off download does nothing.
set -eu

# the maintainer's release key (make signing-key writes it; the same as release-signers)
SIGNERS='iterm-enhancer-release namespaces="iterm-enhancer-release" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILXEkieNRfwJy2GLquHnQptia7ORU0+2kR4mUr+GB80A'

fail() { echo "install.sh: $*" >&2; exit 1; }

main() {
    [ "$(uname -s)" = Darwin ] || fail "the iterm-enhancer runs on macOS"
    [ "$(id -u)" != 0 ] || fail "run it as your user, not root (it installs into your home folder)"
    base="${FB_RELEASE_URL:-https://github.com/extractumio/iterm-extension/releases}/latest/download"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    echo "Downloading the iterm-enhancer …"
    curl -fsSL "$base/iterm-enhancer-macos.tar.gz" -o "$tmp/package.tar.gz" || fail "download failed: $base"
    curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS" || fail "download failed: $base/SHA256SUMS"
    curl -fsSL "$base/SHA256SUMS.sig" -o "$tmp/SHA256SUMS.sig" || fail "download failed: $base/SHA256SUMS.sig"
    case $SIGNERS in *ssh-*) ;; *) fail "this installer carries no release key: nothing installed" ;; esac
    printf '%s\n' "$SIGNERS" > "$tmp/signers"
    ssh-keygen -Y verify -f "$tmp/signers" -I iterm-enhancer-release -n iterm-enhancer-release \
        -s "$tmp/SHA256SUMS.sig" < "$tmp/SHA256SUMS" >/dev/null 2>&1 \
        || fail "the release is not signed by the release key: nothing installed"
    want=$(awk '$2 == "iterm-enhancer-macos.tar.gz" { print $1 }' "$tmp/SHA256SUMS")
    got=$(shasum -a 256 "$tmp/package.tar.gz" | awk '{ print $1 }')
    [ -n "$want" ] && [ "$want" = "$got" ] || fail "checksum mismatch: nothing installed"
    release=$(awk '$1 == "#" && $2 == "release" { print $3 }' "$tmp/SHA256SUMS")
    [ -n "$release" ] || fail "the signed list names no release: nothing installed"
    installed="$HOME/.iterm-enhancer/builds/current/BUILD"
    if [ -f "$installed" ]; then
        current=$(cat "$installed")
        if awk -v incoming="$release" -v current="$current" 'BEGIN {
            if (incoming !~ /^v[0-9]+(\.[0-9]+)*$/ || current !~ /^v[0-9]+(\.[0-9]+)*$/) exit 1
            sub(/^v/, "", incoming); sub(/^v/, "", current)
            n = split(incoming, a, "."); m = split(current, b, ".")
            for (i = 1; i <= n || i <= m; i++) {
                if (a[i]+0 < b[i]+0) exit 0
                if (a[i]+0 > b[i]+0) exit 1
            }
            exit 1
        }'; then
            fail "the release $release is older than the installed $current: nothing installed"
        fi
    fi
    mkdir "$tmp/pkg"
    tar -xzf "$tmp/package.tar.gz" -C "$tmp/pkg"
    "$tmp/pkg/iterm-enhancer/iterm-enhancer" install
    echo "Then in iTerm2: View → Toolbelt → Show Toolbelt, and check Files."
}

main "$@"
