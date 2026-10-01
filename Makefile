# iTerm2 File Browser — build and install.
#   make install    build UI + fbd, install fbd and the iTerm2 AutoLaunch bridge
#   make uninstall  remove them (keeps workspaces.json and token)
#   make test       unit tests (Rust, bridge) + typecheck (UI)

BIN_DIR      := $(HOME)/.local/bin
AUTOLAUNCH   := $(HOME)/Library/Application Support/iTerm2/Scripts/AutoLaunch
BRIDGE       := fb_bridge.py
APP_DIR      := $(HOME)/Library/Application Support/iterm-filebrowser

.PHONY: all ui fbd install uninstall test restart clean-registrations

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
	mkdir -p "$(BIN_DIR)" "$(AUTOLAUNCH)"
	install -m 755 fbd/target/release/fbd "$(BIN_DIR)/fbd"
	mkdir -p "$(APP_DIR)/bridge"
	rm -rf "$(APP_DIR)/bridge/fbbridge"
	cp -R bridge/fbbridge "$(APP_DIR)/bridge/fbbridge"
	install -m 644 bridge/$(BRIDGE) "$(AUTOLAUNCH)/$(BRIDGE)"
	@echo "Installed. Restart iTerm2 or run Scripts → AutoLaunch → $(BRIDGE)"

# Relaunch the bridge after an install: the new one stops the old one and its fbd (AC-30).
restart:
	osascript -e 'tell application "iTerm2" to launch API script named "$(BRIDGE)"'

uninstall:
	-pkill -f "AutoLaunch/$(BRIDGE)"
	-pkill -x fbd
	rm -rf "$(BIN_DIR)/fbd" "$(AUTOLAUNCH)/$(BRIDGE)" "$(APP_DIR)/bridge" \
	  "$(HOME)/Library/Application Support/iTerm2/DynamicProfiles/iterm-filebrowser.json"
	@echo "Removed fbd and the AutoLaunch bridge. State kept in ~/Library/Application Support/iterm-filebrowser"
	@echo "To remove the Files entry from iTerm2's Toolbelt menu: quit iTerm2, then run 'make clean-registrations'"

# iTerm2 keeps registered tools in its preferences; it must not be running.
clean-registrations:
	python3 scripts/clean_registrations.py
