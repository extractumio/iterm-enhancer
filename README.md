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
make install    # builds the UI and fbd, installs ~/.local/bin/fbd and the AutoLaunch bridge
make restart    # start the bridge now instead of restarting iTerm2 (a running one is replaced)
```

Then **View → Toolbelt → Show Toolbelt** and check **Files**. `make uninstall` removes
the binary and the bridge; state stays in `~/Library/Application Support/iterm-filebrowser`.

## How it works

```
iTerm2 ──Python API──▶ fb_bridge.py ──POST /internal/state──▶ fbd (Rust, 127.0.0.1:47821)
 (focus, cwd, theme)    (AutoLaunch)  ◀─SSE /internal/commands─┘         │ SSE /api/events
                                                                          ▼
                                             Toolbelt web view: tree · tabs · editor
```

| Part | Path | Role |
|---|---|---|
| Bridge | `bridge/fb_bridge.py` + `bridge/fbbridge/` | iTerm2 AutoLaunch script and its package: starts `fbd`, registers the tool, tracks the focused pane, resolves its cwd and theme, shows the Toolbelt in new windows, opens viewer windows, types into the terminal on request |
| Backend | `fbd/` | Rust (axum): listings, file operations, per-pane workspaces, FSEvents watcher, SSE; serves the embedded UI |
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
| `FB_APP_DIR` | `~/Library/Application Support/iterm-filebrowser` | token and workspace folder |
| `FB_LOG` | `info` | log level |
| `FB_BIN` | `~/.local/bin/fbd` | the bridge starts this fbd binary |
| `FB_AUTO_TOOLBELT` | `1` | `0` stops showing the Toolbelt in new windows |

Logs: `~/Library/Logs/iterm-filebrowser/{fbd,bridge}.log`.

## Troubleshooting

| The panel says | Why | Fix |
|---|---|---|
| **Backend not running** | `fbd` is not up: the bridge is stopped or iTerm2's Python API is off | Enable the Python API; `make restart`; see `~/Library/Logs/iterm-filebrowser/` |
| **Not following iTerm2** | `fbd` runs but no bridge reports the focused pane (the bridge was stopped or hangs); the tree still works but no longer follows `cd` | `make restart` or Scripts → AutoLaunch → fb_bridge.py; see `bridge.log`. A bridge exits with its iTerm2 and a new one replaces a leftover one |
| **Viewer window failed: … did not load as a browser** | iTerm2 could not load the "Files Viewer" profile as a browser profile, even after the bridge reloaded it | Install iTerm2's browser plugin (see iTerm2's web browser documentation), then ⌘-click again; `bridge.log` has the details |
| **Outdated panel link** | This panel was opened with a token fbd no longer accepts (the `token` file was deleted or `FB_PORT` changed while it was open) | Toggle View → Toolbelt → Files, or restart iTerm2. The bridge re-registers the tool with the current link at every start |

iTerm2 keeps every registered Toolbelt tool in its preferences and has no API to remove
one. The bridge keeps all entries that point at fbd up to date; after `make uninstall`,
quit iTerm2 and run `make clean-registrations` to remove them.

## Develop and test

```bash
make test                                  # builds the UI, then cargo test, typecheck, UI unit tests
cd ui && npx playwright-core install chromium-headless-shell   # once, for the browser test
cd ui && node test/e2e_panel.mjs           # panel checks in a real browser against a private fbd
python3 scripts/e2e_cwd.py 10              # cd → panel latency in bash / tmux / tmux -CC (opens an iTerm2 window)
python3 scripts/e2e_terminal.py            # Insert Path / Open Terminal Here / refusals (opens an iTerm2 window)
python3 scripts/e2e_windows.py             # Toolbelt in new windows, viewer window (opens iTerm2 windows)
scripts/security_check.sh                  # refused requests against the installed fbd
scripts/make_big_dir.sh /tmp/fb-big 500000 && scripts/bench_ls.sh /tmp/fb-big
```

Contributor rules: [CLAUDE.md](CLAUDE.md) (also read by AI agents as `AGENTS.md`).

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
