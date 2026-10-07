# iterm-enhancer — build and install from a checkout (users install the package:
# curl … install.sh | sh, then the iterm-enhancer command; README).
#   make install    build the package from this checkout and install it (first install or
#                   upgrade; a build that does not come up healthy is rolled back)
#   make upgrade    the same
#   make rollback   back to the previous build
#   make uninstall  remove builds and scripts (keeps workspaces.json and token)
#   make toolchain  the pinned cross toolchain (zig, cargo-zigbuild) for the Linux helpers
#   make package    the release package in dist/package (tarball, install.sh, SHA256SUMS)
#   make release TAG=vX.Y.Z   tag HEAD, build, sign (key via ssh-agent and the Keychain) and publish
#   make signing-key       the maintainer's release key (once)
#   make test       unit tests (Rust, bridge, installer) + typecheck and unit tests (UI)

ifeq ($(shell id -u),0)
$(error Run make as your user, not root: the install lives in your home folder)
endif

BRIDGE := fb_bridge.py
# one id per set of sources (bridge, fbd, UI), compiled into fbd and the UI (AC-33)
BUILD  := $(shell python3 scripts/install.py id)
export FB_BUILD := $(BUILD)
# which fbd sources: an agent on a remote host works with this fbd when the ids match (AC-37)
export FB_AGENT_ID := $(shell python3 scripts/agents.py id)

.PHONY: all ui fbd install upgrade rollback uninstall test restart clean-registrations toolchain agents package release signing-key

all: fbd

# exactly the lockfile, no install scripts (AC-40)
ui/node_modules: ui/package.json ui/package-lock.json
	cd ui && npm ci --ignore-scripts --no-fund --no-audit
	touch $@

ui: ui/node_modules
	cd ui && npm run -s build

fbd: ui
	cd fbd && cargo build --locked --release

test: ui
	cd fbd && cargo test --locked
	python3 -m unittest discover -s bridge/tests
	cd ui && npx tsc -p . && npm test

install: ui
	python3 scripts/package.py --stage
	dist/package/iterm-enhancer/iterm-enhancer install

# Remote hosts (AC-37 … AC-40): the pinned cross toolchain once, then the package carries
# the helpers for macOS arm64/x86_64 and Linux x86_64/arm64
toolchain:
	python3 scripts/agents.py toolchain

agents: ui
	python3 scripts/agents.py build

package: ui
	python3 scripts/package.py

release: ui
	@test -n "$(TAG)" || { echo "usage: make release TAG=v0.13.0"; exit 2; }
	python3 scripts/release.py "$(TAG)"

# the maintainer's release key, once (run it in your own terminal: it asks for a passphrase)
signing-key:
	python3 scripts/signing_key.py

upgrade: install

rollback:
	python3 scripts/install.py rollback

# Relaunch the bridge without installing: the new one stops the old one and its fbd (AC-30).
restart:
	osascript -e 'tell application "iTerm2" to launch API script named "$(BRIDGE)"'

uninstall:
	python3 scripts/install.py uninstall

# iTerm2 keeps registered tools in its preferences; it must not be running.
clean-registrations:
	python3 scripts/clean_registrations.py
