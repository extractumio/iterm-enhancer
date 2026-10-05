# iTerm2 terminal state restoration

Status: implemented in the checkout; automatic startup received pragmatic review, with final validation recorded below. Real reboot/remote/display owner validation remains open. No installed build or live preferences were changed. Integration checks create and remove only owned fixture windows and servers.

## Outcome and user flow

1. Installation configures the three requested iTerm2 settings once and reports actual changes. Ordinary bridge restarts preserve later user choices.
2. Automatic save and restore is enabled on a fresh install; no checkbox, checkpoint or button is required. An explicit opt-out persists across upgrades.
3. Open iTerm2 after a reboot or unclean app exit. The bridge reserves the latest durable source, waits for native topology to settle and automatically reconciles missing terminals. A durably observed normal exit skips automatic reconstruction on the next same-boot launch; saving stays enabled and manual Restore remains available. Starting tmux manually is unnecessary.
4. All panels display one job, per-pane deviations and **Retry / reconcile**. Existing live panes are preserved; previously running applications are not relaunched by this restorer.
5. For current-session navigation, use native **⌘⇧O**, then `/f`, or the Files **Find terminal session** button. Requested **⌘⇧T** is already native **Undo Close** and is preserved.

A checkpoint is reconstruction metadata, not a process-memory snapshot. Shell startup files, SSH configuration and native iTerm2 restoration can independently execute programs.

## Platform findings and decisions

| Facility | Finding | Decision |
|---|---|---|
| Session Restoration | Local job servers can survive an iTerm2 restart, but not a Mac reboot | Enable the native baseline; automatically reconcile missing terminals on each new iTerm2 process |
| Window Arrangements | Save/restore windows, tabs and splits; public API has no launch-policy override or subset selection | Use public window/tab/split APIs with controlled commands instead |
| Python scripting | AutoLaunch, Toolbelt, menu/RPC integration and session model | Extend the existing bridge; no extra daemon, framework or dependency |
| Native IDs | iTerm2 serializes session GUIDs into arrangements | Adopt only exact live GUIDs; never infer identity from cwd/profile |
| Ended-session restart | Installed API reruns the original program despite updated Command properties | Preserve ended panes/history; create controlled replacements separately |
| Window/Tab/Session APIs | Installed SDK creates nested splits, applies preferred cell sizes/frames, groups tabs | Use the tested installed interface, without newer App.async_apply_layout |
| Transactions | Owned list/create proof deadlocked the installed API | Use fresh discovery plus immediate pre-creation inventory; document the remaining narrow late-reopening race |
| Shell Integration | Provides host/cwd for supported remote shells | Store trustworthy observations; reset provenance when the SSH process changes |
| Open Quickly | Already searches live sessions and activates a split pane | Reuse native popup/shortcut |
| tmux -CC | iTerm2 owns native views of the tmux graph | Attach once per server/session, reconcile exact pane IDs; never close tmux tabs as cleanup |

Platform inspected: iTerm2 3.7.3 with its bundled Python SDK. Native GUID persistence is supported by versioned source inspection; actual persistence through a reboot is not yet experimentally verified.

## Settings and cleanup

| Setting/data | Behavior |
|---|---|
| Startup | OpenArrangementAtStartup=false; OpenNoWindowsAtStartup=false; preserve unrelated OpenBookmark |
| Session restoration | RunJobsInServers=true; unset already means the enabled default; changes need an iTerm2 restart |
| Automatic integration | Enable for supported shell/SSH profiles; exclude browsers, applications, shell scripts and Apple's /bin/bash |
| Installation request | Private UUID request/result files, durable consumption and write-intent journal; change-only notice, errors reported separately |
| Live inventory | Independent complete all-window/all-tab sweep every five seconds, including minimized/buried sessions |
| Window cleanup | Purge state, claims, watcher references and bridge caches after 60 seconds of confirmed absence; inventory gaps reset grace |
| Files workspace TTL | Continuous expiry after 14 idle days by default; live inactive panes retained, unknown tmux identities conservatively protected |

macOS **Close windows when quitting an application** must also be off for system reopening. The installer does not change macOS settings. Automatic integration applies to new sessions; remote shells must support/report it.

## Architecture

| Owner | Implementation | Responsibility |
|---|---|---|
| Python bridge | recovery_capture.py, recovery_tmux_capture.py, recovery_liveness.py, recovery_lifecycle.py, recovery_exit.py | Whole-app topology, fresh process/TTY identity, root exit/closure evidence, cwd provenance and tmux graphs |
| Python bridge | recovery_startup.py, recovery_runner.py, recovery_native.py, recovery_tmux.py, recovery_recipes.py | Startup gate, controlled launches, identity reconciliation and deviations |
| Rust fbd | checkpoint.rs, recovery_store.rs, recovery_startup.rs, recovery_lifecycle.rs, recovery.rs | Strict schema, immutable generations, closure projection, startup reservation, bounded retention, one durable job and authenticated routes |
| Files panel | recovery.ts | Global checkpoint controls/history/report; events ordered by backend instance/revision |

Capture polls separately from the focused Files follower. It rejects a generation if native/tmux topology changes during collection, marks unavailable metadata stale/unknown, and skips unchanged writes. Five seconds is the polling interval, not a guaranteed durability latency; metadata timeouts can extend a sweep. Capture waits for durable startup completion and pauses during recovery. Unknown liveness never supplies process cwd/argv; proven ended panes are excluded, while their history remains in iTerm2.

Closure tracking runs independently during capture and recovery. Fresh root PID/start/TTY proof arms Darwin kqueue NOTE_EXITSTATUS; fresh complete native inventory confirms ordinary shell completion or pane/tab/window disappearance while the same iTerm2 process and API remain operational. A 1.5-second grace precedes durable retirement; volatile evidence blocks creation immediately. GUID retirement projects all retained immutable sources, pinned startup and retries; exact journal/validated creation aliases cover missing acknowledgements. Fresh live-root or native tmux-client proof reverses retirement for Undo. Retired IDs remain while referenced by retained snapshots/current aliases and are collected even on unchanged capture (16,384 IDs and 4 MiB index maximum).

Controlled shells close on completion. Controlled SSH/tmux diagnostics remain after nonzero exits; capture pauses so Retry and later process launches retain the connection source. Successful controlled connection completion retires its identity and closes only its exact marked history. Nonzero remote exits are ambiguous and conservatively require explicit close to retire. A new proven live attempt clears old diagnostic failures; waiting SSH authentication retains the validated recipe rather than recording a local startup shell. Closed control-mode panes disable reconstruction of their physical server graph; remaining references report unsupported. Closing a plain tmux client does not remove physical server panes.

Known remote-directory targets keep capture paused until a generated SSH connection has a verified remote cwd, including password/MFA and metadata gaps. A live replacement identified by its recipe's source GUID or validated exact marker supersedes old diagnostics even before journal acknowledgement; closing those diagnostics cannot retire the new connection. History GC may expire an old creation marker without preventing native inventory or root enrollment.

Public termination notifications have no exit reason. API loss, signal death or unobserved events do not prove deliberate close; no absolute distinction exists during bridge gaps, app teardown before process exit, or loss before durable acknowledgement. Recovery covers lost iTerm2/Mac state rather than continuous revival of failed individual processes while the app remains operational. Terminal output, command history and persisted exit codes are unnecessary.

Startup is keyed by boot identity plus the iTerm2 PID/start time. Same-process bridge/backend restarts and build upgrades never repeat completed startup; legacy indexes already in this process also skip restoration. A new process reserves the latest snapshot, including a deliberate empty layout, before capture. Three seconds of stable native topology are required within each bounded 30-second attempt. A durable pending reservation resumes the same job after failure; its source remains pinned and capture stays paused. Job completion and delayed progress must belong to the current epoch. Interrupted jobs offer Retry; opting out releases the gate without reconstructing.

A separate kqueue watch arms the exact iTerm2 PID/start identity before API shutdown. Proven normal status zero is recorded via the authenticated private `POST /internal/recovery/exit {epoch:"boot-a:4242:100"}` before backend shutdown. The index stores only `clean_exit` run identity. The next same-boot startup commits `phase:"done", skipped:true`, clears the marker and interrupts any old running actor before index commit. Same-process retries cannot interrupt a new manual actor. Native layout must still settle before capture; the source remains available manually even if it is corrupt. A changed boot always permits automatic recovery. API loss waits at most three seconds for the exit event; delivery times out in one second. SIGTERM takeover drains already queued evidence before backend shutdown, without treating a still-live parent as Quit.

This is a normal-process-exit policy, not a public Quit hook: AppleScript Quit, orderly app updates and same-boot logout also skip. Signal/nonzero exits, denied enrollment, missing bridge/backend and failed persistence are unknown and retain automatic recovery. Native iTerm2/macOS reopening is independent; the plugin never closes those windows. Cmd-Option-Q can discard native saved window state. The user's iTerm2 is never quit by the fixtures.

Storage is separate from workspaces.json. Each snapshot is at most 4 MiB; history keeps at most 64 entries and 128 MiB, with 4 MiB transient headroom. One stable/nonempty candidate from each of the last eight epochs and the job's source is pinned. Epochs use boot identity plus iTerm2 PID/kernel start, not bridge PID. Empty startup and partial closing generations cannot remove the pinned nonempty source. No reliance on delayed termination notifications or a last-second shutdown hook.

Writes use 0600 temporary files, file fsync, rename and parent-directory fsync. Failed commits leave the previous index authoritative. Quota recovery recommits that index before deleting orphan files. Corrupt indexes/journals block writes and preserve files. Disk I/O/lock waits run off async workers.

One job identity per snapshot is durable before any creation. Exact original GUIDs, creation-time profile markers and journal GUIDs are the only adoption evidence. Checked binding rejects ambiguity in either enumeration order. A fresh inventory precedes creation; public nontransactional creation still leaves a narrow race with native reopening. User-added topology, busy panes and changed directories are preserved and reported. Retry never types commands into live sessions.

## Connection support

| Saved pane | Delivered behavior | Verification limit |
|---|---|---|
| Native local shell/application | Ordered tabs/splits, shell cwd, preferred cell sizes and window frame; application name recorded, no application replay | 15-window fixture passed; exact Spaces/display/fullscreen behavior and real reboot remain unverified |
| Native ended pane reopened by iTerm2 | Keep history, open a controlled replacement, exclude proven ended history from future capture | Owned second-relaunch/projection proof passed; uncertain process identities are preserved with a report |
| Missing/inaccessible directory | Controlled shell falls back to home or root; report/terminal diagnostic; owned idle partial retries accept only expected fallback | Local native/private tmux and generated remote bootstrap tests; real remote fallback remains unverified |
| Ordinary SSH | Narrow destination/options recipe, current interactive authentication; quoted remote cwd bootstrap only for known observations | Recipe/provenance/quoting tests passed; real password/MFA/host-key fixtures unverified |
| Unsupported SSH/wrapper/remote command | Shell placeholder plus explicit unresolved report, no saved argv replay | Allowlist rejection tested |
| Surviving local tmux | PID/kernel-start and session-creation verification; attach to existing jobs | Private real server identity tests passed |
| Lost local tmux | Private socket, empty config, shells only, saved window indices/layout/cwd and remapped pane IDs | Real nested four-pane/two-window reconstruction and retry passed |
| Plain local tmux clients | Independent grouped sessions restore the recorded window without switching other clients; unobservable client-local pane selection falls back to the window active pane with a report | Private TTY client proof passed; read-only tmux 3.6a formats do not expose client-local active-pane state |
| Recorded tmux session groups | Preserve actual session IDs; recreate one shared graph per authoritative group, with separate clients/control gateways | Two capture/restore generations and same-pane independent TTY clients tested |
| Surviving remote tmux -CC | Strict-host-key batch SSH probe verifies host/socket/PID/start/session creation before attachment | Mock identity/auth failure tests passed; actual remote/control-mode attachment unverified |
| Lost remote tmux | Explicit manual recovery report; no guessed remote reconstruction | Intentionally unsupported |
| Former Claude/Codex session | Name only; shell in saved cwd | Agent-session resume excluded; no transcript/history reads |

Remote recipes exclude executable -o overrides and arbitrary remote commands. Generated scripts explicitly invoke /bin/sh -c, including when the account shell is fish. SSH disables RemoteCommand and PermitLocalCommand; user SSH configuration is still an independent trust boundary. All tmux helpers/attachments forbid server startup with -N; only explicit initial private creation uses an empty config. Remote attachment repeats identity verification after SSH authentication. Remote tmux is never rebuilt automatically. iTerm2 control-mode tabs must match the expected pane set; failed gateways remain available for manual inspection/retry.

Files expanded folders/editor tabs are not migrated across newly assigned terminal IDs. Live-cache purge removes closed window records; bounded history remains immutable, with durably closed GUIDs excluded from every reconstruction candidate.

## Development order and exit criteria

| Order | Dependency | Exit criterion |
|---|---|---|
| Platform research and pragmatic critique | Project inspection, primary sources | Native reconstruction/launch/identity policy documented |
| Spec scenarios | Reviewed design | AC-43–50 defined before implementation |
| Setup, liveness, navigation | Existing bridge/API | Isolated preferences/cache/popup tests pass |
| Capture/storage | Native and tmux metadata | Bounds, durability, corruption, quotas and protected-source tests pass |
| Reconstruction | Snapshot schema/store | Owned native and private tmux fixtures pass without old-job replay |
| Global controls/retry | Durable job and markers | Shared progress, concurrent joining and missing-ack reconciliation pass |
| Owner validation | Delivered checkout installed by request | Real reboot, SSH authentication, remote/-CC and display/fullscreen results recorded |

## Review and evidence

Pragmatic reviewed the design before implementation and independently reviewed automatic startup, failures and repeated restoration. Final verdict: no concrete blockers; approval conditional on final passing checks and explicit validation limits. Review fixes preserve authoritative tmux groups, independent clients, original/private cache separation and durable reset before rebuilding a lost private server. Accepted findings also include strict creation identities, preserved previous-run sources, controlled launch policy, remote cwd provenance, bounded storage and honest external-validation limits. Findings and resulting regressions are recorded in the spec's Definition of Done. No review advice was silently rejected.

2026-10-04: final make test passed 91 Rust, 240 Python and 30 UI tests without new warnings; private browser checks passed 182/182 plus 25 upstream review regressions. The 15-native-window fixture passed 17 checks, generated-connection failure three, platform proof seven, and private cwd/terminal/security checks passed (security 33/33). Seven real private group regressions passed. tmux -CC was SKIP. Normal quit/skip received design and implementation approval; ten bridge and nine Rust regressions cover evidence, delivery and durability. Real Cmd-Q/native saved-state disposal remain owner validation. Detailed evidence is recorded in [the specification](specs/iterm-file-browser.md). Relevant reproducible commands:

- make test
- cd ui && node test/e2e_panel.mjs
- cd ui && node test/e2e_review.mjs
- python3 scripts/e2e_isolated.py
- python3 scripts/e2e_recovery.py
- python3 scripts/e2e_connection_failure.py
- Bundled iTerm2 Python runtime: scripts/e2e_restore_platform.py

The isolated runner executes cwd, terminal and security checks against private fbd/state; integration child entry points reject live state before starting a backend. The native fixture owns 15 windows and checks topology/cwd/frames, identity adoption, removed directories and partial retry, simulated process epochs and bridge/backend/build restarts. Real controlled-shell exit (0 and 7) and an explicit GUI close retire panes before another capture; later epochs never recreate them. The connection fixture executes the generated SSH command against a private stub returning 255 and verifies retained diagnostics, explicit Retry, and upgrade/new-epoch recovery. It reads no SSH configuration and makes no network connection. Unit regressions cover Undo, unavailable exit-status enrollment, PID reuse/signals, expired markers, retired-source garbage collection, failed connection generations and preserved remote cwd during authentication. The fixtures do not reboot the machine or quit iTerm2 and are not a mixed remote proof.

The implementation includes origin/main at d0e4b6f, preserving upstream security/data-integrity changes and existing local edits. A pre-merge stash remains as a safety copy. Versioned release notes are in [v0.18.0](releases/v0.18.0.md). Release preparation does not install the build or change live preferences; those require a separate installation request.

Noticed, not fixed: pre-existing spec-lint length/coverage/example failures; Files workspace identity migration and full remote/control-mode/reboot validation remain open. A private follower exited during a parallel general test run; the sequential final run passed, but its deleted temporary log leaves the cause unknown. Public APIs provide no shutdown durability barrier; changes after the last successful sweep can be lost.

## Primary sources

- [iTerm2 3.7.3 Undo Close binding](https://github.com/gnachman/iTerm2/blob/v3.7.3/sources/MainMenu/MainMenu.xib), [Apple process/controlling-TTY ABI](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/proc_info.h).

- [Session Restoration](https://iterm2.com/documentation-restoration.html), [Window Arrangements](https://iterm2.com/python-api/arrangement.html), [General settings](https://iterm2.com/documentation-preferences-general.html).
- [Shell Integration](https://iterm2.com/documentation-shell-integration.html), [variables](https://iterm2.com/documentation-variables.html), [hooks](https://iterm2.com/python-api/tutorial/hooks.html), [lifecycle API](https://iterm2.com/python-api/lifecycle.html).
- [Window API](https://iterm2.com/python-api/window.html), [Tab API](https://iterm2.com/python-api/tab.html), [Session API](https://iterm2.com/python-api/session.html), [RPC registration](https://iterm2.com/python-api/registration.html).
- [iTerm2 3.7.3 PTYSession source](https://github.com/gnachman/iTerm2/blob/v3.7.3/sources/PTYSession/PTYSession.m): arrangement GUID persistence and restart's original-program behavior.
- [tmux integration](https://iterm2.com/documentation-tmux-integration.html), [tmux manual](https://man.openbsd.org/tmux.1), [tmux 3.6a active-pane implementation](https://github.com/tmux/tmux/blob/3.6a/cmd-select-pane.c).
- Existing tmux-only alternatives: [tmux-resurrect](https://github.com/tmux-plugins/tmux-resurrect), [process restore policy](https://github.com/tmux-plugins/tmux-resurrect/blob/master/docs/restoring_programs.md), [tmux-continuum](https://github.com/tmux-plugins/tmux-continuum).
