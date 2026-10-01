# iTerm2 File Browser — build and install.
#   make install    build, install as a versioned build and switch to it (first install or
#                   upgrade; a build that does not come up healthy is rolled back)
#   make upgrade    the same
#   make rollback   back to the previous build
#   make uninstall  remove builds and scripts (keeps workspaces.json and token)
#   make agent HOST=<ssh alias>   make a remote host ready for the Files panel
#   make toolchain / agents       the pinned cross toolchain / agents for all platforms
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

.PHONY: all ui fbd install upgrade rollback uninstall test restart clean-registrations toolchain agents agent release

all: fbd

ui/node_modules: ui/package.json
	cd ui && npm install --no-fund --no-audit
	touch $@

ui: ui/node_modules
	cd ui && npm run -s build

fbd: ui
	cd fbd && cargo build --release

test: ui
	cd fbd && cargo test
	python3 -m unittest discover -s bridge/tests
	cd ui && npx tsc -p . && npm test

install: fbd
	python3 scripts/agents.py build --recorded
	python3 scripts/install.py install --build $(BUILD)

# Remote hosts (AC-37 … AC-39): the pinned cross toolchain once, agents for all platforms,
# and one command that makes a host ready: make agent HOST=<ssh alias>
toolchain:
	python3 scripts/agents.py toolchain

agents: ui
	python3 scripts/agents.py build

agent: ui
	@test -n "$(HOST)" || { echo "usage: make agent HOST=<ssh alias>"; exit 2; }
	python3 scripts/agent.py "$(HOST)"

# the macOS agents of release $(TAG) (the runner attaches the Linux ones)
release: ui
	@test -n "$(TAG)" || { echo "usage: make release TAG=v0.12.0"; exit 2; }
	python3 scripts/release.py $(TAG)

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
