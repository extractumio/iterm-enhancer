// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! fbd — backend of the iTerm2 "Files" Toolbelt panel.
//!
//! Serves the embedded UI and a token-protected JSON API on 127.0.0.1. The iTerm2 bridge
//! (fb_bridge.py) pushes the focused pane's state to /internal/state; panels receive it
//! over a WebSocket (/api/ws, socket.rs), the agent relay and scripts over SSE (/api/events).

mod agent;
mod api_fs;
mod api_state;
mod auth;
mod checkpoint;
mod events;
mod files;
mod http;
mod listing;
mod liveness;
mod local;
mod ops;
mod panels;
mod remote;
mod recovery;
mod recovery_store;
mod recovery_lifecycle;
#[cfg(test)]
mod recovery_lifecycle_tests;
mod recovery_startup;
#[cfg(test)]
mod recovery_startup_tests;
#[cfg(test)]
mod recovery_quit_tests;
#[cfg(test)]
mod recovery_tests;
mod socket;
mod termtext;
mod update;
mod watch_web;
mod web;
mod watcher;
mod workspace;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::middleware;
use axum::routing::{get, post, put};
use axum::Router;
use parking_lot::Mutex;
use serde_json::{json, Value};

use events::Bus;
use files::Roots;
use listing::Cache;
use watcher::Watcher;
use workspace::Store;

/// The build this binary is (AC-33): set by `make` from the sources; "dev" otherwise.
/// `FB_BUILD_ID` overrides it at run time, for tests that play an upgrade.
const BUILD: &str = match option_env!("FB_BUILD") {
    Some(b) => b,
    None => "dev",
};

/// The build this fbd answers as (the compiled one unless a test overrides it).
fn build_id() -> &'static str {
    static ID: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    ID.get_or_init(|| std::env::var("FB_BUILD_ID").unwrap_or_else(|_| BUILD.to_string()))
}

struct Config {
    port: u16,
    /// running as a remote host's agent (AC-37): served on a Unix socket
    agent: bool,
    /// the Host header requests must carry
    host: String,
    token: String,
    bridge_secret: Option<String>,
    text_max: u64,
}

#[derive(Default)]
struct Term {
    /// last state pushed by the bridge (the key window's), as sent to panels
    state: Value,
    /// last state of each window, for panels that live in a window that is not key (AC-36)
    windows: HashMap<String, (Value, Instant)>,
    version: u64,
    last_push: Option<Instant>,
    /// what panels were last told about the bridge (AC-30)
    announced_alive: bool,
    setup: Value,
    /// the newest release the bridge found, `{"latest": tag}`, or null (AC-51)
    update: Value,
    /// web access as the bridge reports it, `{"enabled", "password", "urls", "error"}`, or null (AC-52)
    web: Value,
}

impl Term {
    /// The bridge pushes at least every 5 s while alive.
    fn bridge_alive(&self) -> bool {
        self.last_push.is_some_and(|i| i.elapsed() < Duration::from_secs(10))
    }

    /// True once when the bridge falls silent, so panels stop showing it as followed.
    fn went_silent(&mut self) -> bool {
        let silent = self.announced_alive && !self.bridge_alive();
        if silent {
            self.announced_alive = false;
            self.version += 1;
        }
        silent
    }
}

pub struct App {
    cfg: Config,
    bus: Bus,
    cache: Arc<Cache>,
    store: Arc<Store>,
    roots: Roots,
    term: Mutex<Term>,
    started: Instant,
    denied: std::sync::atomic::AtomicU64,
    /// commands for the bridge (type into the terminal)
    commands: tokio::sync::broadcast::Sender<Value>,
    watcher: Option<Arc<Watcher>>,
    tickets: Tickets,
    /// files sent to the viewer window before its page connected (drained by the page)
    viewer_pending: Mutex<Vec<api_state::ViewBody>>,
    /// the web app's Files panels, watched beside the focused pane (AC-53)
    web_panes: watch_web::WebPanes,
    /// which window each panel lives in (AC-36)
    panels: panels::Panels,
    live: Mutex<liveness::Live>,
    recovery: Arc<Mutex<recovery_store::Store>>,
    /// remote hosts' agents (AC-37)
    remotes: remote::Remotes,
    /// the build the bridge last said it is (AC-33)
    bridge_build: Mutex<Value>,
    /// open event streams by transport (AC-41)
    streams: socket::Streams,
}

pub type Shared = Arc<App>;

/// One-time codes that put a viewer window's URL in place of the token (AC-26).
#[derive(Default)]
struct Tickets(Mutex<Vec<(String, Instant)>>);

impl Tickets {
    const TTL: Duration = Duration::from_secs(60);

    fn issue(&self) -> String {
        let code = auth::random_hex::<12>();
        let mut t = self.0.lock();
        t.retain(|(_, at)| at.elapsed() < Self::TTL);
        t.push((code.clone(), Instant::now()));
        code
    }

    fn redeem(&self, code: &str) -> bool {
        let mut t = self.0.lock();
        t.retain(|(_, at)| at.elapsed() < Self::TTL);
        let Some(at) = t.iter().position(|(c, _)| !code.is_empty() && local::same(c, code)) else { return false };
        t.swap_remove(at);
        true
    }
}

impl App {
    /// Watch what the focused pane shows: its root, expanded folders, folders of open tabs.
    /// A remote pane's folders are watched by its host's agent (AC-37), not here.
    fn refresh_watch(&self) {
        let (key, host) = {
            let t = self.term.lock();
            (t.state["key"].as_str().map(str::to_string), t.state["host"].as_str().map(str::to_string))
        };
        let focused = key.map(|k| (host, self.store.get(&k, None)));
        let web = self.web_panes.keys().into_iter().map(|k| (watch_web::host_of(&k), self.store.get(&k, None)));
        let (local, remote) = watch_web::plan(focused.into_iter().chain(web).collect());
        for (h, r) in remote {
            remote::watch(&self.remotes, &h, r.root, r.expanded, r.files);
        }
        if let Some(w) = &self.watcher {
            w.set(local);
        }
    }

    /// A change made through a panel: tell every panel now, without waiting for FSEvents.
    fn changed(&self, dirs: &[PathBuf], files: &[PathBuf], moved: &[(PathBuf, PathBuf)]) {
        events::publish_change(&self.cache, &self.bus, dirs, files, moved);
    }
}

fn env<T: std::str::FromStr>(key: &str, default: T) -> T {
    std::env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

/// What the panel uses, over TCP (and an agent's socket): the token decides (auth.rs).
fn routes(app: Shared) -> Router {
    use api_fs::*;
    use api_state::*;
    Router::new()
        .route("/api/hello", get(local::hello))
        .route("/api/state", get(get_state))
        .route("/api/events", get(events))
        .route("/api/ws", get(socket::socket))
        .route("/api/ls", get(ls))
        .route("/api/file", get(file).put(save_file).layer(axum::extract::DefaultBodyLimit::max(save_body_limit(app.cfg.text_max))))
        .route("/api/raw", get(raw))
        .route("/api/fs/mkdir", post(fs_mkdir))
        .route("/api/fs/touch", post(fs_touch))
        .route("/api/fs/rename", post(fs_rename))
        .route("/api/fs/trash", post(fs_trash))
        .route("/api/os/open", post(os_open))
        .route("/api/os/reveal", post(os_reveal))
        .route("/api/terminal/{action}", post(terminal))
        .route("/api/workspace", get(get_workspace).put(put_workspace))
        .route("/api/prefs", get(get_prefs).put(put_prefs))
        .route("/api/recovery", get(recovery::get).put(recovery::configure))
        .route("/api/recovery/save", post(recovery::save))
        .route("/api/recovery/restore", post(recovery::restore))
        .route("/api/health", get(health))
        .route("/api/watch", put(watch))
        .route("/api/view/open", post(view_open))
        .route("/api/remote/{action}", post(remote_action))
        .route("/api/view/pending", get(view_pending))
        .route("/api/ui/toolbelt-width", post(toolbelt_width))
        .route("/api/ui/open-quickly", post(open_quickly))
        .route("/api/update", post(update::choose))
        .route("/api/web", post(web::choose))
        .route("/api/panel/claim", post(panels::panel_claim))
        .route("/api/panel/bind", post(panels::panel_bind))
        .route("/ticket", get(ticket))
        .fallback(http::ui)
        .layer(middleware::from_fn_with_state(app.clone(), remote::route)) // after the auth guard
        .layer(middleware::from_fn_with_state(app.clone(), auth::guard))
        .with_state(app)
}

/// What the bridge and the installer use, only on the private socket (local.rs, AC-07).
fn bridge_routes(app: Shared) -> Router {
    use api_state::*;
    Router::new()
        .route("/health", get(health))
        .route("/internal/bound", post(panels::internal_bound))
        .route("/internal/remote", post(remote::internal_remote))
        .route("/internal/viewer-open", post(internal_viewer_open))
        .route("/internal/error", post(internal_error))
        .route("/internal/state", post(internal_state))
        .route("/internal/liveness", post(liveness::inventory))
        .route("/internal/setup", post(liveness::setup))
        .route("/internal/update", post(update::internal_update))
        .route("/internal/web", post(web::internal_web))
        .route("/internal/recovery", get(recovery::get))
        .route("/internal/recovery/snapshot/{id}", get(recovery::snapshot))
        .route("/internal/recovery/capture", post(recovery::capture).layer(axum::extract::DefaultBodyLimit::max(4 << 20)))
        .route("/internal/recovery/job", post(recovery::progress))
        .route("/internal/recovery/error", post(recovery::error))
        .route("/internal/recovery/epoch", post(recovery::epoch))
        .route("/internal/recovery/lifecycle", post(recovery::lifecycle))
        .route("/internal/recovery/exit", post(recovery::normal_exit))
        .route("/internal/recovery/startup", post(recovery::startup))
        .route("/internal/recovery/startup/begin", post(recovery::startup_begin))
        .route("/internal/recovery/startup/finish", post(recovery::startup_finish))
        .route("/internal/commands", get(internal_commands))
        .layer(middleware::from_fn_with_state(app.clone(), local::guard))
        .with_state(app)
}

/// Save the workspaces and exit (SSE streams never end, so there is no graceful drain).
fn exit_with(store: &Store, reason: &str) -> ! {
    store.flush();
    agent::cleanup();
    tracing::info!(event = "stop", reason);
    std::process::exit(0);
}

#[tokio::main]
async fn main() {
    if std::env::args().any(|a| a == "--version") {
        println!("{BUILD}");
        return;
    }
    if std::env::args().any(|a| a == "--agent-id") {
        println!("{}", agent::AGENT_ID);
        return;
    }
    unsafe { libc::umask(0o077) }; // what fbd writes (workspaces, logs, sockets) is the user's alone
    let agent = agent::socket_arg().map(|s| agent::start(&s));
    tracing_subscriber::fmt()
        .with_env_filter(std::env::var("FB_LOG").unwrap_or_else(|_| "info".into()))
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .init();

    let dir = agent.as_ref().map_or_else(auth::app_dir, |a| a.dir.clone());
    let port: u16 = env("FB_PORT", 47821);
    // the port first: if another program held it, the token may have reached it (AC-07)
    let (listener, foreign) = match agent {
        Some(_) => (None, false),
        None => local::bind_port(&dir, port).await.map_or((None, false), |(l, f)| (Some(l), f)),
    };
    let bus = Bus::new();
    let cache = Cache::new(env("FB_LIST_CACHE_MB", if agent.is_some() { 32usize } else { 128 }) << 20);
    let watcher = Watcher::start(cache.clone(), bus.clone())
        .inspect_err(|e| tracing::warn!(event = "watch.unavailable", error = %e))
        .ok();
    let app = Arc::new(App {
        cfg: Config {
            port,
            agent: agent.is_some(),
            host: if agent.is_some() { agent::HOST.to_string() } else { format!("127.0.0.1:{port}") },
            token: agent.as_ref().map_or_else(|| auth::load_token(&dir, foreign || std::env::var_os("FB_NEW_TOKEN").is_some()), |a| a.token.clone()),
            bridge_secret: std::env::var("FB_BRIDGE_SECRET").ok().filter(|s| s.len() >= 16),
            text_max: env("FB_TEXT_MAX_BYTES", 10u64 << 20),
        },
        cache,
        store: Store::load(dir.join("workspaces.json"), env("FB_WORKSPACE_TTL_DAYS", 14), bus.clone()),
        roots: Roots::parse(&std::env::var("FB_WRITABLE_ROOTS").unwrap_or_else(|_| "$HOME:/tmp".into())),
        bus,
        term: Mutex::new(Term { state: json!({}), ..Default::default() }),
        panels: Default::default(),
        live: Default::default(),
        recovery: Arc::new(Mutex::new(recovery_store::Store::load(dir.join("recovery")))),
        remotes: Default::default(),
        bridge_build: Mutex::new(Value::Null),
        streams: Default::default(),
        started: Instant::now(),
        denied: Default::default(),
        commands: tokio::sync::broadcast::channel(32).0,
        watcher,
        tickets: Tickets::default(),
        viewer_pending: Mutex::new(Vec::new()),
        web_panes: Default::default(),
    });

    if let Some(a) = agent {
        let store = app.store.clone();
        agent::exit_with_stdin(move || exit_with(&store, "ssh connection closed"));
        let (store, bus) = (app.store.clone(), app.bus.clone());
        agent::exit_when_unused(move || bus.subscribers(), move || exit_with(&store, "no Mac connected for 90 s"));
        ops::NO_FINDER.store(true, std::sync::atomic::Ordering::Relaxed);
        let store = app.store.clone();
        tokio::spawn(async move {
            use tokio::signal::unix::{signal, SignalKind};
            let (mut term, mut hup) = (signal(SignalKind::terminate()).unwrap(), signal(SignalKind::hangup()).unwrap());
            tokio::select! { _ = term.recv() => {}, _ = hup.recv() => {} }
            exit_with(&store, "signal");
        });
        tracing::info!(event = "start", mode = "agent", agent_id = agent::AGENT_ID, pid = std::process::id());
        axum::serve(a.listener, routes(app)).await.unwrap();
        return;
    }
    let listener = listener.expect("bound above");
    socket::raise_open_files();
    if foreign {
        tracing::warn!(event = "token.renewed", reason = "another program held the port");
    }
    let bridge = local::bind(&dir);
    tokio::spawn(axum::serve(bridge, bridge_routes(app.clone())).into_future());
    tracing::info!(event = "start", port, pid = std::process::id(), build = build_id(), bridge = app.cfg.bridge_secret.is_some());
    if app.cfg.bridge_secret.is_some() {
        // started by the bridge: exit with it (iTerm2 quit or the script was stopped)
        let parent = unsafe { libc::getppid() };
        let store = app.store.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(2)).await;
                if unsafe { libc::getppid() } != parent {
                    exit_with(&store, "bridge exited");
                }
            }
        });
    }
    let silence = app.clone();
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(Duration::from_secs(1)).await;
            let mut t = silence.term.lock();
            if t.went_silent() {
                tracing::warn!(event = "bridge.silent");
                silence.bus.send(events::Event::State(api_state::state_of(&t)));
            }
        }
    });
    let store = app.store.clone();
    tokio::spawn(async move {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()).unwrap();
        tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = term.recv() => {} }
        exit_with(&store, "signal");
    });
    axum::serve(listener, routes(app)).await.unwrap();
}

#[cfg(test)]
mod tests {
    use super::*;

    pub(crate) fn app(path: PathBuf) -> Shared {
        Arc::new(App {
            cfg: Config { port: 47832, agent: false, host: "127.0.0.1:47832".into(),
                token: "test-token".into(), bridge_secret: None, text_max: 1024 },
            bus: Bus::new(), cache: Cache::new(1024), store: Store::load(path.clone(), 14, Bus::new()),
            roots: Roots::parse("/tmp"),
            term: Mutex::new(Term { state: json!({}), ..Default::default() }),
            panels: Default::default(), live: Default::default(), remotes: Default::default(),
            recovery: Arc::new(Mutex::new(recovery_store::Store::load(path.with_extension("recovery")))),
            bridge_build: Mutex::new(Value::Null), streams: Default::default(), started: Instant::now(),
            denied: Default::default(), commands: tokio::sync::broadcast::channel(32).0,
            watcher: None, tickets: Default::default(), viewer_pending: Default::default(), web_panes: Default::default(),
        })
    }

    #[test]
    fn bridge_silence_is_announced_once() {
        let mut t = Term { state: json!({}), version: 3, ..Default::default() };
        assert!(!t.went_silent(), "never connected: nothing to announce");
        t.last_push = Some(Instant::now());
        t.announced_alive = true;
        assert!(!t.went_silent(), "a fresh push is alive");
        t.last_push = Instant::now().checked_sub(Duration::from_secs(11));
        assert!(t.went_silent());
        assert_eq!(t.version, 4, "panels see a new state version");
        assert!(!t.went_silent(), "announced once");
    }

    #[test]
    fn tickets_are_single_use() {
        let t = Tickets::default();
        let code = t.issue();
        assert!(t.redeem(&code));
        assert!(!t.redeem(&code));
        assert!(!t.redeem(""));
        assert!(!t.redeem("nope"));
    }

    #[test]
    fn expired_tickets_never_authenticate() {
        let t = Tickets::default();
        let expired = Instant::now() - Tickets::TTL;
        t.0.lock().push(("expired".into(), expired));
        let fresh = t.issue();
        t.0.lock().push(("expired".into(), expired));
        assert!(!t.redeem("unknown"), "expiry cleanup is not redemption");
        t.0.lock().push(("expired".into(), expired));
        assert!(!t.redeem("expired"));
        assert!(!t.redeem(""));
        assert!(t.redeem(&fresh));
        assert!(!t.redeem(&fresh));
    }
}
