// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! fbd — backend of the iTerm2 "Files" Toolbelt panel.
//!
//! Serves the embedded UI and a token-protected JSON API on 127.0.0.1. The iTerm2 bridge
//! (fb_bridge.py) pushes the focused pane's state to /internal/state; panels receive it
//! over SSE (/api/events).

mod api_fs;
mod api_state;
mod auth;
mod events;
mod files;
mod http;
mod listing;
mod ops;
mod panels;
mod watcher;
mod workspace;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::middleware;
use axum::routing::{get, post};
use axum::Router;
use parking_lot::Mutex;
use serde_json::{json, Value};

use events::Bus;
use files::Roots;
use listing::Cache;
use watcher::Watcher;
use workspace::Store;

struct Config {
    port: u16,
    token: String,
    bridge_secret: Option<String>,
    text_max: u64,
}

struct Term {
    /// last state pushed by the bridge (the key window's), as sent to panels
    state: Value,
    /// last state of each window, for panels that live in a window that is not key (AC-36)
    windows: HashMap<String, (Value, Instant)>,
    version: u64,
    last_push: Option<Instant>,
    /// what panels were last told about the bridge (AC-30)
    announced_alive: bool,
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
    viewer_pending: Mutex<Vec<String>>,
    /// which window each panel lives in (AC-36)
    panels: panels::Panels,
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
        let before = t.len();
        t.retain(|(c, at)| c != code && at.elapsed() < Self::TTL);
        t.len() < before && !code.is_empty()
    }
}

impl App {
    /// Watch what the focused pane shows: its root, expanded folders, folders of open tabs.
    fn refresh_watch(&self) {
        let Some(w) = &self.watcher else { return };
        let key = self.term.lock().state["key"].as_str().map(str::to_string);
        w.set(key.map_or_else(Default::default, |k| {
            let p = self.store.get(&k, None);
            watcher::wanted(&p.root, &p.expanded, p.tabs.into_iter().map(|t| t.path))
        }));
    }

    /// A change made through a panel: tell every panel now, without waiting for FSEvents.
    fn changed(&self, dirs: &[PathBuf], files: &[PathBuf], moved: &[(PathBuf, PathBuf)]) {
        events::publish_change(&self.cache, &self.bus, dirs, files, moved);
    }
}

fn env<T: std::str::FromStr>(key: &str, default: T) -> T {
    std::env::var(key).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

fn routes(app: Shared) -> Router {
    use api_fs::*;
    use api_state::*;
    Router::new()
        .route("/api/state", get(get_state))
        .route("/api/events", get(events))
        .route("/api/ls", get(ls))
        .route("/api/file", get(file).put(save_file))
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
        .route("/api/health", get(health))
        .route("/api/view/open", post(view_open))
        .route("/api/view/pending", get(view_pending))
        .route("/api/ui/toolbelt-width", post(toolbelt_width))
        .route("/api/panel/claim", post(panels::panel_claim))
        .route("/api/panel/bind", post(panels::panel_bind))
        .route("/internal/bound", post(panels::internal_bound))
        .route("/ticket", get(ticket))
        .route("/internal/viewer-open", post(internal_viewer_open))
        .route("/internal/error", post(internal_error))
        .route("/internal/state", post(internal_state))
        .route("/internal/commands", get(internal_commands))
        .fallback(http::ui)
        .layer(middleware::from_fn_with_state(app.clone(), auth::guard))
        .with_state(app)
}

/// Save the workspaces and exit (SSE streams never end, so there is no graceful drain).
fn exit_with(store: &Store, reason: &str) -> ! {
    store.flush();
    tracing::info!(event = "stop", reason);
    std::process::exit(0);
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(std::env::var("FB_LOG").unwrap_or_else(|_| "info".into()))
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .init();

    let dir = auth::app_dir();
    let port: u16 = env("FB_PORT", 47821);
    let bus = Bus::new();
    let cache = Cache::new(env("FB_LIST_CACHE_MB", 128usize) << 20);
    let watcher = Watcher::start(cache.clone(), bus.clone())
        .inspect_err(|e| tracing::warn!(event = "watch.unavailable", error = %e))
        .ok();
    let app = Arc::new(App {
        cfg: Config {
            port,
            token: auth::load_token(&dir),
            bridge_secret: std::env::var("FB_BRIDGE_SECRET").ok().filter(|s| s.len() >= 16),
            text_max: env("FB_TEXT_MAX_BYTES", 10u64 << 20),
        },
        cache,
        store: Store::load(dir.join("workspaces.json"), env("FB_WORKSPACE_TTL_DAYS", 14), bus.clone()),
        roots: Roots::parse(&std::env::var("FB_WRITABLE_ROOTS").unwrap_or_else(|_| "$HOME:/tmp".into())),
        bus,
        term: Mutex::new(Term { state: json!({}), windows: HashMap::new(), version: 0, last_push: None, announced_alive: false }),
        panels: Default::default(),
        started: Instant::now(),
        denied: Default::default(),
        commands: tokio::sync::broadcast::channel(32).0,
        watcher,
        tickets: Tickets::default(),
        viewer_pending: Mutex::new(Vec::new()),
    });

    // a previous fbd may still be shutting down (it polls for its bridge every 2 s)
    let mut attempt = 0;
    let listener = loop {
        match tokio::net::TcpListener::bind(("127.0.0.1", port)).await {
            Ok(l) => break l,
            Err(_) if attempt < 15 => {
                attempt += 1;
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
            Err(e) => {
                eprintln!("error: port {port} in use (set FB_PORT): {e}");
                std::process::exit(2);
            }
        }
    };
    tracing::info!(event = "start", port, pid = std::process::id(), bridge = app.cfg.bridge_secret.is_some());
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

    #[test]
    fn bridge_silence_is_announced_once() {
        let mut t = Term { state: json!({}), windows: HashMap::new(), version: 3, last_push: None, announced_alive: false };
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
}
