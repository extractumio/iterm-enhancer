#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
# Set up the owner's GitHub Actions runner for this repository on a Linux VM (AC-39), the
# way the owner's other repositories run theirs: its own user (no password, no sudo, no
# Docker), a hardened systemd unit in github-runners.slice, tools in that user's home.
#
# Run as root on the VM, with a registration token on stdin (never on a command line):
#   scp scripts/runner/setup.sh root@<vm>:/root/fb-runner-setup.sh
#   gh api -X POST repos/extractumio/iterm-extension/actions/runners/registration-token --jq .token \
#     | ssh root@<vm> 'bash /root/fb-runner-setup.sh --name vm102-iterm'                (see README)
# Re-running it is safe: existing pieces are kept, the registration is replaced.
set -euo pipefail

REPO_URL="https://github.com/extractumio/iterm-extension"
RUNNER_USER="github-runner-iterm"
RUNNER_VERSION="2.337.0"
RUST_TOOLCHAIN="1.98.1"
NAME="vm102-iterm"
while [ $# -gt 0 ]; do
    case "$1" in
        --name) NAME="$2"; shift 2 ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
# the token: the first line of stdin, kept out of the process list and the shell history
IFS= read -r TOKEN || true
[ -n "${TOKEN:-}" ] || { echo "registration token expected on stdin" >&2; exit 1; }

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates git build-essential python3 python3-venv tar >/dev/null
if ! command -v gh >/dev/null; then  # GitHub's own apt repository, for `gh release` in release.yml
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list
    apt-get update -qq && apt-get install -y -qq gh >/dev/null
fi

echo "== user $RUNNER_USER"
id "$RUNNER_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$RUNNER_USER"
passwd -l "$RUNNER_USER" >/dev/null
chmod 750 "/home/$RUNNER_USER"
HOME_DIR="/home/$RUNNER_USER"
DIR="$HOME_DIR/actions-runner"

echo "== Rust $RUST_TOOLCHAIN for $RUNNER_USER"
sudo -u "$RUNNER_USER" -H bash -c "
    set -e
    command -v ~/.cargo/bin/rustup >/dev/null || curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain $RUST_TOOLCHAIN >/dev/null
    ~/.cargo/bin/rustup toolchain install $RUST_TOOLCHAIN --profile minimal >/dev/null
    ~/.cargo/bin/rustup default $RUST_TOOLCHAIN >/dev/null
"

echo "== runner $RUNNER_VERSION in $DIR"
sudo -u "$RUNNER_USER" -H bash -c "
    set -e
    mkdir -p $DIR && cd $DIR
    if [ ! -x ./config.sh ]; then
        curl -fsSL -o runner.tgz https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-linux-x64-$RUNNER_VERSION.tar.gz
        tar xzf runner.tgz && rm runner.tgz
    fi
"
if [ -f "$DIR/.runner" ]; then  # a previous registration: stop it before replacing it
    (cd "$DIR" && ./svc.sh stop >/dev/null 2>&1 || true; ./svc.sh uninstall >/dev/null 2>&1 || true)
fi
# the token goes through the environment of one command only
sudo -u "$RUNNER_USER" -H env ACTIONS_RUNNER_INPUT_TOKEN="$TOKEN" bash -c "
    cd $DIR && ./config.sh --unattended --replace --url $REPO_URL --name $NAME --labels vm102 --work _work >/dev/null
"
unset TOKEN
(cd "$DIR" && ./svc.sh install "$RUNNER_USER" >/dev/null)
UNIT="$(cd "$DIR" && cat .service)"

echo "== limits and hardening for $UNIT"
mkdir -p "/etc/systemd/system/$UNIT.d"
cat > "/etc/systemd/system/$UNIT.d/resources.conf" <<EOF
[Service]
Environment=PATH=$HOME_DIR/.cargo/bin:/usr/local/bin:/usr/bin:/bin
Environment=FB_TOOLCHAIN=$HOME_DIR/.cache/fb-toolchain
Environment=CARGO_BUILD_JOBS=2
Nice=10
CPUWeight=50
MemoryMax=2G
MemorySwapMax=0
OOMScoreAdjust=500
PrivateTmp=yes
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=tmpfs
BindPaths=$HOME_DIR
InaccessiblePaths=-/root
EOF
cat > "/etc/systemd/system/$UNIT.d/slice.conf" <<EOF
[Service]
Slice=github-runners.slice
EOF
[ -f /etc/systemd/system/github-runners.slice ] || cat > /etc/systemd/system/github-runners.slice <<EOF
[Slice]
MemoryMax=3G
MemorySwapMax=0
CPUWeight=50
EOF
systemctl daemon-reload
systemctl restart "$UNIT"
systemctl --no-pager --lines=0 status "$UNIT" | head -3
echo "runner $NAME registered for $REPO_URL"
