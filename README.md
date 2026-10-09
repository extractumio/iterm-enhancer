# iterm-enhancer

An IDE-style **Files** panel in every iTerm2 window (View → Toolbelt → Files) that always shows
the folder of the pane you work in, and an optional **web terminal** that brings your iTerm2
sessions and files to a browser on your iPhone, iPad or another computer.

![The Files panel beside a terminal: the tree of the pane's folder and a rendered README](docs/images/file-browser-screen.png)

## Key features

- **Follows your terminal**: the tree re-roots to the focused pane's folder in bash, tmux and
  tmux -CC within 0.2–0.5 s; each pane keeps its own tree, selection and tabs.
- **Remote file browsing**: a tmux -CC session on another machine shows that machine's files
  after one click; nothing to install there by hand. [Remote hosts](#remote-hosts)
- **Web terminal**: every iTerm2 window, tab and pane in a browser, with typing, hot keys,
  scrollback, the profile's colors, and the Files panel with its editor; password-protected,
  HTTPS through Tailscale. [Web access](#web-access)
- **View and edit**: highlighted code, rendered Markdown and HTML, images; save with ⌘S and a
  conflict check; a large viewer window (⌘-click); new, rename, Trash, copy path, `cd` here.
- **Session window recovery**: windows, tabs, splits, folders and ssh/tmux connections come
  back after a reboot. [Save and restore](#save-and-restore-terminals)
- **Fast**: 500,000-file folders open in 0.3 s; 100 windows stay responsive; ~31 MB memory.
- **Safe and simple**: one-line install, signed releases, upgrades with one command;
  writes only under `$HOME` and `/tmp`, delete moves to the Trash. [SECURITY.md](SECURITY.md)

## The Files panel

- **cwd**: follows bash, tmux and tmux -CC panes; plain ssh and mosh panes freeze the tree and
  show `REMOTE` (tmux -CC hosts: [Remote hosts](#remote-hosts)).
- **Viewing**: syntax highlighting by file name; Markdown rendered by default (Source shows
  it highlighted); HTML rendered sandboxed, scripts never run; images in a tab. Links open
  the linked file in a tab (at its `#section`); web links open in your browser.
- **Editing**: ● marks unsaved tabs, ⌘S saves; a file changed on disk asks Overwrite / Reload /
  Cancel; closing an unsaved tab asks first.
- **Viewer window**: ⌘-click a file (or ⌘↩, Open in Window) for a large window with tabs and
  the full path; it uses a "Files Viewer" browser profile the bridge adds (`make uninstall`
  removes it).
- **Tree**: type-specific icons; ⌥→ / ⌥← expand or collapse everything below a folder (up to
  200 folders, depth 8; skips `node_modules`, `.git`, build output); ⇧/⌥-click select; F2,
  ⌥N / ⌥⇧N, ⌘⌫; the context menu copies paths, reveals in Finder, inserts paths into the
  terminal or `cd`s it to a folder. Changes by other programs show within about a second.
- **Layout and theme**: the last Toolbelt width and tree/viewer split become the default for
  new windows; colors and font follow the pane's iTerm2 profile.
- **Find a session**: ⌘⇧O (or Find terminal session) opens iTerm2's Open Quickly; `/f` plus a
  title, folder or host searches open sessions.

## Install

```bash
curl -fsSL https://github.com/extractumio/iterm-enhancer/releases/latest/download/install.sh | sh
```

Then in iTerm2: **View → Toolbelt → Show Toolbelt** and check **Files**. Needs macOS 14+,
iTerm2 3.5+ with **Settings → General → Magic → Enable Python API**, no build tools (tmux 3.2+
for tmux panes). The installer checks the release's signature and installs into
`~/.iterm-enhancer/` plus the bridge in iTerm2's AutoLaunch. It also turns on iTerm2's
session restoration and shell integration for shell and SSH profiles, and says which settings
it changed (a changed restoration setting takes effect after restarting iTerm2).

Pane state lives 14 days without use (`FB_WORKSPACE_TTL_DAYS`); closed windows' caches go after
60 s.

## Upgrade

```bash
~/.iterm-enhancer/bin/iterm-enhancer upgrade              # the latest signed release
~/.iterm-enhancer/bin/iterm-enhancer upgrade --to v0.18.0 # a given one
~/.iterm-enhancer/bin/iterm-enhancer status               # running build, bridge, panels, hosts, web
```

The bridge restarts by itself and open panels reload (after saving unsaved edits). `panels`
counts connected panels against windows: each install gives every window a new panel and
iTerm2 keeps the old ones hidden until it quits.

About once a day the bridge asks GitHub for the latest release (nothing of yours is sent).
A newer one shows a chip such as `↑ v0.21.0` in the panel header: **Copy upgrade command**,
**Skip** it, or **Don't check for updates** (undo: delete
`~/.iterm-enhancer/state/no-update-check`). Only releases signed by the maintainer's key
install; its fingerprint is in every release's notes.

## Save and restore terminals

On by default. Every 5 s the bridge saves inactive windows, ordered tabs, split trees,
folders, window frames and supported ssh/tmux connections. After a reboot, open iTerm2: once
its own reopening settles, the bridge restores the latest saved state and adopts panes that
are still alive (no need to start tmux first).

- **Session Window Recovery** (the clock button in the Files header): **Automatic saving**
  (switch, **Save a new checkpoint now**) and **Restore** (checkpoints that stayed unchanged
  for 30 s plus the newest of each run, newest selected; **Restore selected**, **Retry /
  reconcile**).
- **A normal quit** (⌘Q, AppleScript, updates, logout) skips the automatic restore at the next
  launch in the same boot; the dialog offers it by hand. A reboot or crash restores.
- **Shells** reopen at their folders (a missing one opens home, with a report); deliberately
  closed panes stay closed. **SSH** reconnects supported destinations (interactive login,
  remote folder from shell integration). **tmux** servers that survived keep their jobs; lost
  local ones come back with shells; lost remote ones need manual recovery.
- **Not restored**: running programs (Claude, Codex, …; recorded by name), output, history,
  environment, exact Spaces placement. Checkpoints live in `~/.iterm-enhancer/state/recovery`
  (at most 64 or 128 MiB).

Real reboots, interactive SSH and tmux -CC recovery still need owner validation; details and
limits are in the [spec](docs/specs/iterm-enhancer.md).

## Uninstall

```bash
~/.iterm-enhancer/bin/iterm-enhancer hosts remove devbox.example   # first, per remote host (optional)
~/.iterm-enhancer/bin/iterm-enhancer uninstall
```

This stops the bridge and fbd and removes the builds, the command and the AutoLaunch entry;
your settings and logs stay in `~/.iterm-enhancer` (delete it to remove them). To take
**Files** out of the Toolbelt menu, quit iTerm2 and run `make clean-registrations` in a checkout.

## Installed layout and commands

```text
~/.iterm-enhancer/
  bin/      iterm-enhancer (add this folder to your PATH), fbd
  builds/   one folder per build; current, previous
  logs/     bridge.log, fbd.log
  state/    token, workspaces.json, agents.json (remote hosts), web.json (web access; password as a hash)
```

```bash
iterm-enhancer                       # status
iterm-enhancer upgrade | rollback | uninstall
iterm-enhancer hosts [enable <ssh args> | remove <host>]
iterm-enhancer web [on [--port N] [--host A] | off | password]
```

From a checkout: `make install` (and `make toolchain` once for the Linux helpers), `make
rollback`, `make uninstall`. Building needs Rust (stable) and Node.js 20+.

## Remote hosts

A tmux -CC pane on another machine (for example `ssh -tt devbox.example "tmux -CC new -A -s
work"`) can show that machine's files. The first time you focus it, the panel asks:
**devbox is a remote host. Browse its files here?** — **Enable** copies a small helper (about
6 MB) over your ssh connection, with your ssh settings, keys and jump hosts; nothing to run by
hand, no password. The panel then shows `devbox:/path` and works as for local files: open,
edit, save, create, rename, Trash, live refresh.

- The helper (`~/.iterm-enhancer/bin/fbd-agent` on the host) runs only while your Mac is
  connected, as your user, writes only under your home and /tmp, and opens no network port.
- Upgrades on the Mac update every host's helper by themselves.
- **Not now** hides the question until iTerm2 restarts; the context menu has **Browse Files
  of devbox…** and **Remove Helper from devbox…**; from the command line: `iterm-enhancer
  hosts`, `hosts enable devbox.example` (any ssh options), `hosts remove devbox.example`.
- Helpers exist for macOS (arm64, x86_64) and Linux (x86_64, arm64); plain ssh panes and mosh
  stay frozen as `REMOTE`.

## Web access

Your iTerm2 sessions and their files in a browser on another device. Off until you switch it
on, in a terminal or with the globe button in the Files header:

```bash
iterm-enhancer web on        # asks for a password (8+ characters) the first time
iterm-enhancer web           # on or off, its addresses, and any error
iterm-enhancer web off
iterm-enhancer web password  # change it (or FB_WEB_PASSWORD for a script)
```

Open one of the addresses (default port 8765, every network of this Mac; `--port`, `--host`)
and sign in:

- **Sessions**: every window, tab and pane by title and host (blue laptop: this Mac; violet
  server: a remote host). **+** on a window opens a new tab right after the selected
  session, with its profile and in its folder; on a tmux group, a new tmux window there. Each row has a dot in its profile's color; Claude
  Code and Codex sessions show their state (working, needs you, failed, done). **Merge
  windows** under the list moves every tab into one iTerm2 window, and each tmux session's tabs
  into one window of their own, without bringing iTerm2 to the front; iTerm2 cannot undo it
  (drag a tab out of its tab bar to split it again), and a window that closes takes its Files
  panel and any unsaved edits there along.
- **Terminal**: the pane in its profile's colors and font, typing, a hot keys panel (Copy,
  Paste, Tab, ⇧Tab, ^C, Esc, Ctrl, Alt, ⇧←, ⇧↩, arrows…), scrollback, selection. On a phone
  two floating buttons open the keyboard and the hot keys. On a phone lines
  re-flow to the screen (**Wrap**); **Grid** keeps iTerm2's layout, **Fit** scales it to the
  width, **Resize iTerm to this screen** (⋯) changes the Mac's window until you restore it.
- **Names**: the pencil beside the title names the session: its iTerm2 tab and session, and in
  tmux its window and pane title. Clear the name to give it back to iTerm2 and tmux.
- **Links**: click or tap a web address to open it in a new tab, or a file path or name the
  terminal printed (`src/main.rs:42`, `~/notes.md`, `README.md`) to open it in File.
- **Images**: paste one (PNG, JPEG, GIF, WebP, up to 20 MB) and it is saved where the pane's
  shell runs, in `$TMPDIR/iterm-enhancer-paste/` on this Mac or `~/.cache/iterm-enhancer/paste/`
  on a host with the helper; its path is pasted, which Claude Code takes as an image. Files go
  after 7 days.
- **Files** and **File**: this panel for the pane's folder (remote hosts too) and its editor
  (at 1400 px and wider, Files docked on the right and File a window over the terminal);
  web links open in your browser; Finder, apps and typing into a terminal are not offered.

It is plain HTTP: on a network you do not trust, use HTTPS through Tailscale —
`tailscale serve --bg 8765`, then open `https://<mac>.<tailnet>.ts.net/` (it also enables the
Paste button). The password also opens your terminals: choose it like your Mac's. Sign-ins
last 7 days (30 over HTTPS); repeated wrong passwords make an address, then everyone, wait.

## How it works

```
iTerm2 ──Python API──▶ fb_bridge.py ──POST /internal/state──▶ fbd (Rust, 127.0.0.1:47821)
 (focus, cwd, theme)    (AutoLaunch)  ◀─SSE /internal/commands─┘         │ WebSocket /api/ws
                            │                                            ▼
                            └─ web access (optional): browser ⇄ mirror + proxy to fbd ─┘
                                             Toolbelt web view: tree · tabs · editor
```

| Part | Path | Role |
|---|---|---|
| Bridge | `bridge/fb_bridge.py` + `bridge/fbbridge/` | iTerm2 AutoLaunch script: starts `fbd`, registers the tool, follows the focused pane's cwd and theme, opens viewer windows, types into the terminal on request, serves web access when it is on (`fbbridge/web/`) |
| Backend | `fbd/` | Rust (axum): listings, file operations, per-pane workspaces, FSEvents watcher, events over a WebSocket; serves the embedded UI; stays on loopback |
| UI | `ui/` | TypeScript: virtual tree, CodeMirror 6 viewer/editor, markdown-it rendering |
| Spec | `docs/specs/iterm-enhancer.md` | Behavior as BDD scenarios, design, limits, test evidence |

## Keys

| Key | Action |
|---|---|
| ↑ ↓ ← → · PgUp PgDn Home End | move, expand / collapse |
| ⌥→ / ⌥← | expand / collapse a folder and everything below it |
| ↩ | open file / toggle folder |
| F2 | rename |
| ⌥N / ⌥⇧N | new file / new folder |
| ⌘⌫ | move to Trash |
| ⌥⌘C / ⌥⇧⌘C | copy path / relative path |
| ⌘-click, ⌘↩ | open the file in the viewer window |
| ⇧-click, ⌥-click | range / toggle selection |
| ⌘S | save |
| ⌥W, ⌃Tab | close tab, next tab |
| `/`, Esc | filter, clear filter |

⌘N, ⌘W and ⌘T are left to iTerm2 on purpose (⌘W would close the terminal session).

## Configuration

Environment of `fbd` (the bridge passes its own environment through):

| Variable | Default | Effect |
|---|---|---|
| `FB_PORT` | `47821` | loopback port |
| `FB_WRITABLE_ROOTS` | `$HOME:/tmp` | folders the panel may write to |
| `FB_LIST_CACHE_MB` | `128` | memory for cached listings |
| `FB_TEXT_MAX_BYTES` | `10485760` | larger files open read-only, first 1 MB |
| `FB_WORKSPACE_TTL_DAYS` | `14` | idle pane state is dropped after this |
| `FB_APP_DIR` | `~/.iterm-enhancer/state` | token and workspace folder (tests use their own) |
| `FB_LOG` | `info` | log level |
| `FB_BIN` | its build's `fbd` | the bridge starts this fbd binary instead |
| `FB_AUTO_TOOLBELT` | `1` | `0` stops showing the Toolbelt in new windows |
| `FB_BUILD_ID` | the compiled build | lets a test play another build (AC-34) |

Logs: `~/.iterm-enhancer/logs/{fbd,bridge}.log`.

## Troubleshooting

| The panel says | Why | Fix |
|---|---|---|
| **Backend not running** | `fbd` is not up: the bridge is stopped or iTerm2's Python API is off | Enable the Python API; `make restart`; see `~/.iterm-enhancer/logs/` |
| **Not following iTerm2** | `fbd` runs but no bridge reports the focused pane (the bridge was stopped or hangs); the tree still works but no longer follows `cd` | `make restart` or Scripts → AutoLaunch → fb_bridge.py; see `bridge.log`. A bridge exits with its iTerm2 and a new one replaces a leftover one |
| **Viewer window failed: … did not load as a browser** | iTerm2 could not load the "Files Viewer" profile as a browser profile, even after the bridge reloaded it | Install iTerm2's browser plugin (see iTerm2's web browser documentation), then ⌘-click again; `bridge.log` has the details |
| **Install of … failed** / **Upgrade to … failed; rolled back** | the new build did not report healthy within 10 s | see `bridge.log` and `fbd.log`; the build before keeps running |
| **iTerm2 did not start it: … Automation** | macOS does not let your terminal control iTerm2 | System Settings → Privacy & Security → Automation: allow iTerm2 for your terminal app, then `make install` |
| **Outdated panel link** | This panel was opened with a token fbd no longer accepts (the `token` file was deleted or `FB_PORT` changed while it was open) | Toggle View → Toolbelt → Files, or restart iTerm2. The bridge re-registers the tool with the current link at every start |
| Web access: **another device gets no answer** (this Mac works) | the macOS Firewall holds connections for iTerm2's own Python, which serves web access | allow it when macOS asks, or System Settings → Network → Firewall → Options (`~/Library/Application Support/iTerm2/uv/python/…/python3.12`); over Tailscale, also check that its access rules let the device reach this Mac |

iTerm2 keeps every registered Toolbelt tool in its preferences and has no API to remove
one. The bridge keeps all entries that point at fbd up to date; after `make uninstall`,
quit iTerm2 and run `make clean-registrations` to remove them.

## Develop and test

```bash
make test                                  # builds the UI, then cargo test, bridge + installer tests, typecheck, UI unit tests
cd ui && npx playwright-core install chromium-headless-shell   # once, for the browser test
cd ui && node test/e2e_panel.mjs           # panel checks in a real browser against a private fbd
python3 scripts/e2e_isolated.py            # iTerm2 checks with a private fbd and state: cd latency (e2e_cwd),
                                           # terminal commands (e2e_terminal), refused requests (security_check);
                                           # opens its own windows; the live installation is never used
python3 scripts/e2e_windows.py             # Toolbelt in new windows, viewer window, panel per window (opens iTerm2 windows; needs iTerm2 in front)
python3 scripts/e2e_remote.py [--amd64]    # remote helper end to end against a Linux container with sshd (Docker)
make package                               # the release package in dist/package (tarball, install.sh, SHA256SUMS)
scripts/make_big_dir.sh /tmp/fb-big 500000 && scripts/bench_ls.sh /tmp/fb-big
```

A private fbd's `FB_APP_DIR` must be a short path (its Unix socket path is limited to 104
bytes; `/tmp/<name>` works, a deep temporary folder does not). Python tests run from the
repository root as `FB_APP_DIR=$(mktemp -d /tmp/fbt-XXXXXX) python3 -m unittest bridge.tests.test_web` (or `discover -s bridge/tests`):
the private state folder keeps tests out of the live log, token and workspaces, as `make test` does.

Contributor rules: [CLAUDE.md](CLAUDE.md) (also read by AI agents as `AGENTS.md`).

### CI and releases on the owner's runner

Workflows run only on the owner's self-hosted Linux runner (`[self-hosted, linux, x64]`),
never on GitHub-hosted runners, and never for pull requests (this repository is public: a
fork must not run code on the runner); `bridge/tests/test_workflows.py` enforces it,
with actions pinned to commits in `.github/actions-allowlist.json`. `ci.yml` runs the
tests on Linux and builds the Linux agents on every push to `main`. A tag `v*` runs
`release.yml`: tests and the Linux agents, read-only: no workflow can write to the
repository or a release, so code running on the runner cannot change what users install.
`make release TAG=v…` on the Mac publishes the release: it builds the package users install
(all four helpers) from the tag and attaches it with `install.sh`, `SHA256SUMS` and its
signature; signing, the release key and the runner setup are in
[docs/RELEASING.md](docs/RELEASING.md).

## License

Dual-licensed:

- **[GNU AGPL-3.0](LICENSE)**: free for personal use, open-source projects and anyone who
  meets its terms (publish your source when you distribute it or offer a modified version
  over a network).
- **[Commercial license](LICENSE-COMMERCIAL.md)**: for companies that use it internally
  without the AGPL obligations or ship it in closed products.

File-type icons: [Tabler Icons](https://tabler.io/icons) 3.48.0, MIT License (`ui/src/icons/LICENSE`).

Copyright © 2026 Gregory Zemskov and contributors. Licensing:
[info@extractum.io](mailto:info@extractum.io).

Contributions are accepted under the agreement in [CONTRIBUTING.md](CONTRIBUTING.md).
