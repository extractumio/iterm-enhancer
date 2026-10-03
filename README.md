# iTerm2 File Browser

An IDE-style **Files** panel inside every iTerm2 window (View → Toolbelt → Files) that
always shows the directory of the pane you are working in. New windows open with it shown.

## What the panel does

- **Follows the cwd** of the focused pane in bash, tmux and tmux -CC; the tree re-roots in
  about 0.2–0.5 s. Remote sessions (ssh, remote tmux) freeze the tree and show `REMOTE`.
- **Large folders**: 500,000 files open in 0.3 s, scrolling to any position takes 9 ms per
  page, filtering by name 0.33 s; the backend uses about 31 MB of memory.
- **Viewing**: syntax highlighting by file name. Markdown opens rendered with proper
  typography by default; the Source toggle shows highlighted source, including fenced code
  blocks. HTML files render too (sandboxed, scripts never run). Images open in a tab. Links
  inside Markdown and HTML open the linked document in a new tab (at its `#section`), web
  links open in your browser.
- **Editing**: a ● marks an unsaved tab, ⌘S saves. If the file changed on disk since you
  opened it, you get "Overwrite / Reload / Cancel". Closing an unsaved tab asks first.
- **Viewer window**: ⌘-click a file (or ⌘↩, or "Open in Window") to read and edit it in a
  large separate window; more files open there as tabs, and it reopens at its last size. A
  bar on top shows the file's full path with a copy button. The window uses a "Files
  Viewer" browser profile that the bridge adds to iTerm2 (`make uninstall` removes it).
- **Expand and collapse all**: header buttons, or ⌥→ / ⌥← (⌥-click the arrow) on one
  folder. Expanding stops at 200 folders and depth 8, and skips `node_modules`, `.git`,
  build output and folders with more than 500 entries; a toast says what it skipped.
- **File-type icons** for code, config, data, docs, images, archives, keys and more.
- **IDE operations**: new file or folder (⌥N / ⌥⇧N; `a/b` creates nested folders), rename
  with F2, move to Trash with ⌘⌫. Select with ⇧-click and ⌥-click. The context menu copies
  paths, reveals in Finder, inserts the path into the terminal, and `cd`s the terminal to a
  folder.
- **Live refresh**: changes made by other programs show up within about a second.
- **Memory per pane**: every iTerm2 pane or tmux pane keeps its expanded folders,
  selection, scroll position and tabs, across backend restarts too. A `cd` resets the
  tree and keeps the tabs.
- **Layout**: the latest Toolbelt width and tree/viewer split become the default for new
  windows; every open window keeps its own until it is closed.
- **Theme and font** come from the pane's iTerm2 profile.

## Requirements

- macOS 14+, iTerm2 3.5+ with **Settings → General → Magic → Enable Python API**
- Rust (stable) and Node.js 20+ to build; tmux 3.2+ for tmux panes

## Install

```bash
curl -fsSL https://github.com/extractumio/iterm-extension/releases/latest/download/install.sh | sh
```

It downloads the latest release, checks its checksum, and installs it into one folder,
`~/.iterm-filebrowser/`, plus the bridge entry in iTerm2's AutoLaunch. Then in iTerm2:
**View → Toolbelt → Show Toolbelt** and check **Files**. Needs macOS, iTerm2 with its Python
API enabled (Settings → General → Magic), and no build tools.

```text
~/.iterm-filebrowser/
  bin/      iterm-filebrowser (the command; add this folder to your PATH), fbd
  builds/   one folder per build; current, previous
  logs/     bridge.log, fbd.log
  state/    token, workspaces.json, agents.json (enabled hosts)
```

An install from before this layout (`~/.local/lib`, `~/.local/bin`, `~/Library/Application
Support/iterm-filebrowser`, `~/Library/Logs/iterm-filebrowser`) is moved on the next
upgrade; the token and your workspaces come along.

```bash
iterm-filebrowser                 # status: what runs, which builds, which remote hosts
iterm-filebrowser upgrade         # the latest release (checked the same way); open panels reload by themselves
iterm-filebrowser rollback        # back to the build before
iterm-filebrowser uninstall       # remove it; your settings and logs stay in ~/.iterm-filebrowser
```

From a checkout: `make install` builds the same package and installs it (`make toolchain`
once for the Linux helpers); `make rollback`, `make uninstall`.

## Remote hosts

A tmux -CC pane on another machine, for example from `ssh -tt ai4 "tmux -CC new-session
-A -s work"`, can show that machine's files. The first time you focus such a pane, the
Files panel asks:

> **ai4 is a remote host.** Browse its files here? This copies a small helper (about 6 MB)
> to ~/.iterm-filebrowser/bin on ai4 over your ssh connection; it runs only while you use it.
> **[Enable]** [Not now]

**Enable** is all. Nothing is run on the remote host by hand and nothing needs a password:
the bridge uses the very ssh command the tmux session was started with (destination, port,
user, key, jump host) with your own ssh configuration. The panel then shows `ai4:/path` and
works as for local files: open, edit, save, create, rename, Trash, live refresh.

- **On the host**: the helper is `~/.iterm-filebrowser/bin/fbd-agent` (one older version kept),
  its log `~/.iterm-filebrowser/logs/agent.log`. It runs only while your Mac is connected
  (it exits with the ssh connection), as your user, writes only under your home and /tmp,
  and listens on no network port.
- **Authentication** is ssh itself: no pairing, no keys of its own. Each connection hands the
  helper a fresh token over ssh. The host must accept your key or ssh-agent without a
  prompt and allow socket forwarding (sshd's default).
- **Not now** hides the question until iTerm2 restarts; the panel's menu (right click) has
  **Browse Files of ai4…** any time, and **Remove Helper from ai4…** deletes the helper and
  its log there (and `~/.iterm-filebrowser` once empty; a Mac host keeps its own install).
- **Upgrades** of the Mac side update the helper on the host at its next connection.
- From the command line: `iterm-filebrowser hosts`, `iterm-filebrowser hosts enable ai4`
  (any ssh destination and options, e.g. `-p 2222 alex@10.0.0.5`),
  `iterm-filebrowser hosts remove ai4`.
- Helpers exist for macOS (arm64, x86_64) and Linux (x86_64, arm64; one static file for any
  Ubuntu or Debian). Plain `ssh` panes without tmux -CC, and mosh, stay frozen as "remote".
  On Linux, saving keeps a file's mode and owner, not its extended attributes; Trash is
  `~/.local/share/Trash`.

## How it works

```
iTerm2 ──Python API──▶ fb_bridge.py ──POST /internal/state──▶ fbd (Rust, 127.0.0.1:47821)
 (focus, cwd, theme)    (AutoLaunch)  ◀─SSE /internal/commands─┘         │ WebSocket /api/ws
                                                                          ▼
                                             Toolbelt web view: tree · tabs · editor
```

| Part | Path | Role |
|---|---|---|
| Bridge | `bridge/fb_bridge.py` + `bridge/fbbridge/` | iTerm2 AutoLaunch script and its package: starts `fbd`, registers the tool, tracks the focused pane, resolves its cwd and theme, shows the Toolbelt in new windows, opens viewer windows, types into the terminal on request |
| Backend | `fbd/` | Rust (axum): listings, file operations, per-pane workspaces, FSEvents watcher, events to panels over a WebSocket (up to 100 windows); serves the embedded UI |
| UI | `ui/` | TypeScript: virtual tree, CodeMirror 6 viewer/editor, markdown-it rendering |
| Spec | `docs/specs/iterm-file-browser.md` | Behavior as BDD scenarios, design, limits, test evidence |

**cwd resolution**: plain shell → cwd of the shell (not of `vim`); tmux → `tmux display -c
<tty>`; tmux -CC → `display -t %N` through iTerm2's tmux connection; ssh or another host →
frozen tree.

**Security**: loopback only; a random token in the tool URL; Host and Origin checks; writes
only under `$HOME` and `/tmp`; delete moves to the Trash; raw HTML in Markdown is not
rendered; terminal commands refuse control characters and only reach the pane the panel
shows. See [SECURITY.md](SECURITY.md).

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
| `FB_APP_DIR` | `~/.iterm-filebrowser/state` | token and workspace folder (tests use their own) |
| `FB_LOG` | `info` | log level |
| `FB_BIN` | its build's `fbd` | the bridge starts this fbd binary instead |
| `FB_AUTO_TOOLBELT` | `1` | `0` stops showing the Toolbelt in new windows |
| `FB_BUILD_ID` | the compiled build | lets a test play another build (AC-34) |

Logs: `~/.iterm-filebrowser/logs/{fbd,bridge}.log`.

## Troubleshooting

| The panel says | Why | Fix |
|---|---|---|
| **Backend not running** | `fbd` is not up: the bridge is stopped or iTerm2's Python API is off | Enable the Python API; `make restart`; see `~/.iterm-filebrowser/logs/` |
| **Not following iTerm2** | `fbd` runs but no bridge reports the focused pane (the bridge was stopped or hangs); the tree still works but no longer follows `cd` | `make restart` or Scripts → AutoLaunch → fb_bridge.py; see `bridge.log`. A bridge exits with its iTerm2 and a new one replaces a leftover one |
| **Viewer window failed: … did not load as a browser** | iTerm2 could not load the "Files Viewer" profile as a browser profile, even after the bridge reloaded it | Install iTerm2's browser plugin (see iTerm2's web browser documentation), then ⌘-click again; `bridge.log` has the details |
| **Install of … failed** / **Upgrade to … failed; rolled back** | the new build did not report healthy within 10 s | see `bridge.log` and `fbd.log`; the build before keeps running |
| **iTerm2 did not start it: … Automation** | macOS does not let your terminal control iTerm2 | System Settings → Privacy & Security → Automation: allow iTerm2 for your terminal app, then `make install` |
| **Outdated panel link** | This panel was opened with a token fbd no longer accepts (the `token` file was deleted or `FB_PORT` changed while it was open) | Toggle View → Toolbelt → Files, or restart iTerm2. The bridge re-registers the tool with the current link at every start |

iTerm2 keeps every registered Toolbelt tool in its preferences and has no API to remove
one. The bridge keeps all entries that point at fbd up to date; after `make uninstall`,
quit iTerm2 and run `make clean-registrations` to remove them.

## Develop and test

```bash
make test                                  # builds the UI, then cargo test, bridge + installer tests, typecheck, UI unit tests
cd ui && npx playwright-core install chromium-headless-shell   # once, for the browser test
cd ui && node test/e2e_panel.mjs           # panel checks in a real browser against a private fbd
python3 scripts/e2e_cwd.py 10              # cd → panel latency in bash / tmux / tmux -CC (opens an iTerm2 window)
python3 scripts/e2e_terminal.py            # Insert Path / Open Terminal Here / refusals (opens an iTerm2 window)
python3 scripts/e2e_windows.py             # Toolbelt in new windows, viewer window, panel per window (opens iTerm2 windows; needs iTerm2 in front)
scripts/security_check.sh                  # refused requests against the installed fbd
python3 scripts/e2e_remote.py [--amd64]    # remote helper end to end against a Linux container with sshd (Docker)
make package                               # the release package in dist/package (tarball, install.sh, SHA256SUMS)
scripts/make_big_dir.sh /tmp/fb-big 500000 && scripts/bench_ls.sh /tmp/fb-big
```

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
signature (see "Signed releases").

### Signed releases

Installs and upgrades accept a release only if its `SHA256SUMS` is signed by the
maintainer's release key (an ed25519 ssh key; `ssh-keygen -Y`, built into macOS). Its public
half is in `release-signers` and in `scripts/install.sh`, and every package carries it, so
`iterm-filebrowser upgrade` checks with the key of the build already installed. Once, in
your own terminal:

```bash
make signing-key        # ~/.config/iterm-filebrowser/release-key (FB_RELEASE_KEY); writes the public half
git add release-signers scripts/install.sh && git commit -m "Release key"
ssh-keygen -p -f ~/.config/iterm-filebrowser/release-key                 # give it a passphrase
ssh-add --apple-use-keychain ~/.config/iterm-filebrowser/release-key \
  && ssh-add -d ~/.config/iterm-filebrowser/release-key.pub                # keep it in the Keychain, not loaded
```

Then a release is one command, with no prompt: `make release TAG=vX.Y.Z` refuses unless
the tree is clean, a new version is above every `v*` tag and is made at `origin/main`, and
its release is not published yet; it installs the UI's dependencies with `npm ci
--ignore-scripts`, builds with `cargo --locked`, signs `SHA256SUMS` through ssh-agent (the
key loaded for two minutes with the Keychain's passphrase, then removed), checks the
signature, and only then tags `HEAD`, pushes the tag and publishes the GitHub release once
all its files are attached. A failure before signing leaves nothing public.

Who else could ship code to users: an upgrade installs only what this key signed, and the
key exists only on the maintainer's Mac (and its backups). Publishing needs write access to
the repository, which only the maintainer has; the CI runner has neither. Left: whoever
controls the maintainer's GitHub account (or its `gh` login on that Mac) can replace a
release's `install.sh`, which a *first* `curl | sh` trusts; and a program running as the
maintainer while the Keychain is unlocked can sign. The key's fingerprint is in every
release's notes for those who check a first install by hand. Keep the key and its
passphrase backed up: a new key makes existing installs refuse upgrades until they are
installed again with the one-line installer.

Set the runner up once on a Linux VM (as root; the token travels on stdin):

```bash
scp scripts/runner/setup.sh root@<vm>:/root/fb-runner-setup.sh
gh api -X POST repos/extractumio/iterm-extension/actions/runners/registration-token --jq .token \
  | ssh root@<vm> 'bash /root/fb-runner-setup.sh --name vm102-iterm'
```

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
