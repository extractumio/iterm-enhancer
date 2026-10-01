#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# iTerm2 File Browser: install or upgrade with one line (AC-40)
#
#   curl -fsSL https://github.com/extractumio/iterm-extension/releases/latest/download/install.sh | sh
#
# Downloads the latest release's package and its SHA256SUMS into a temporary folder,
# checks the checksum, and runs the package's own installer (a versioned build in
# ~/.local/lib/iterm-filebrowser, the bridge in iTerm2's AutoLaunch, health check,
# rollback when the new build does not come up). Nothing is run before the check passes.
# Everything happens in main, called on the last line: a cut-off download does nothing.
set -eu

fail() { echo "install.sh: $*" >&2; exit 1; }

main() {
    [ "$(uname -s)" = Darwin ] || fail "the iTerm2 File Browser runs on macOS"
    [ "$(id -u)" != 0 ] || fail "run it as your user, not root (it installs into your home folder)"
    base="${FB_RELEASE_URL:-https://github.com/extractumio/iterm-extension/releases}/latest/download"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    echo "Downloading the iTerm2 File Browser …"
    curl -fsSL "$base/iterm-filebrowser-macos.tar.gz" -o "$tmp/package.tar.gz" || fail "download failed: $base"
    curl -fsSL "$base/SHA256SUMS" -o "$tmp/SHA256SUMS" || fail "download failed: $base/SHA256SUMS"
    want=$(awk '$2 == "iterm-filebrowser-macos.tar.gz" { print $1 }' "$tmp/SHA256SUMS")
    got=$(shasum -a 256 "$tmp/package.tar.gz" | awk '{ print $1 }')
    [ -n "$want" ] && [ "$want" = "$got" ] || fail "checksum mismatch: nothing installed"
    mkdir "$tmp/pkg"
    tar -xzf "$tmp/package.tar.gz" -C "$tmp/pkg"
    "$tmp/pkg/iterm-filebrowser/iterm-filebrowser" install
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) echo "Tip: add ~/.local/bin to your PATH to use the iterm-filebrowser command:"
           echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
    esac
    echo "Then in iTerm2: View → Toolbelt → Show Toolbelt, and check Files."
}

main "$@"
