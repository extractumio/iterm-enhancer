# iTerm2 File Browser — build and install.
#   make install    build, install as a versioned build and switch to it (first install or
#                   upgrade; a build that does not come up healthy is rolled back)
#   make upgrade    the same
#   make rollback   back to the previous build
#   make uninstall  remove builds and scripts (keeps workspaces.json and token)
#   make test       unit tests (Rust, bridge, installer) + typecheck and unit tests (UI)

ifeq ($(shell id -u),0)
$(error Run make as your user, not root: the install lives in your home folder)
endif

BRIDGE := fb_bridge.py
# one id per set of sources (bridge, fbd, UI), compiled into fbd and the UI (AC-33)
BUILD  := $(shell python3 scripts/install.py id)
export FB_BUILD := $(BUILD)

.PHONY: all ui fbd install upgrade rollback uninstall test restart clean-registrations

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
	python3 scripts/install.py install --build $(BUILD)

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
