// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Terminal state from the bridge, SSE to panels, per-pane workspaces and prefs,
//! commands typed into the terminal, health.

use std::convert::Infallible;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use axum::extract::{Path as UrlPath, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::sse::{Event as SseEvent, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::Json;
use futures_util::stream::Stream;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;

use crate::events::Event;
use crate::http::{err, ApiResult};
use crate::termtext;
use crate::workspace::Pane;
use crate::{App, Shared, Term};

pub(crate) fn state_json(app: &App) -> Value {
    state_of(&app.term.lock())
}

/// The state panels see. Send it while holding `term`, so events leave in the order the
/// state changed (a bridge's return must not be overtaken by its silence).
pub fn state_of(t: &Term) -> Value {
    with_status(t, &t.state)
}

/// A pane's state as panels see it: plus versions, bridge status and this fbd's build,
/// so an open panel of another build knows to reload (AC-34).
fn with_status(t: &Term, state: &Value) -> Value {
    let mut s = if state.is_object() { state.clone() } else { json!({}) };
    s["version"] = json!(t.version);
    s["bridge"] = json!(t.bridge_alive());
    s["build"] = json!(crate::build_id());
    if !t.setup.is_null() {
        s["setup_notice"] = t.setup.clone();
    }
    if !t.update.is_null() {
        s["update"] = t.update.clone();
    }
    s
}

#[derive(Deserialize)]
pub struct StateQuery {
    window: Option<String>,
}

/// The key window's state, or the last state of `window` (a panel that lives there,
/// AC-36); an unseen or closed window never borrows another window's pane.
pub async fn get_state(State(app): State<Shared>, Query(q): Query<StateQuery>) -> Json<Value> {
    let t = app.term.lock();
    Json(match q.window.as_deref() {
        Some(w) => with_status(&t, &t.windows.get(w).map(|(s, _)| s.clone()).unwrap_or_else(|| json!({"window": w}))),
        None => state_of(&t),
    })
}

pub async fn internal_state(State(app): State<Shared>, Json(mut body): Json<Value>) -> StatusCode {
    body.as_object_mut().map(|o| o.remove("version"));
    // the bridge says which build it is; one from another build means a half-done upgrade
    if let Some(b) = body.as_object_mut().and_then(|o| o.remove("bridge_build")) {
        let mut seen = app.bridge_build.lock();
        if *seen != b {
            if b.as_str() != Some(crate::build_id()) {
                tracing::warn!(event = "build.mismatch", fbd = crate::build_id(), bridge = %b);
            }
            *seen = b;
        }
    }
    let (key, cwd) = (body["key"].as_str().map(str::to_string), body["cwd"].as_str().map(str::to_string));
    let changed = {
        let mut t = app.term.lock();
        t.last_push = Some(Instant::now());
        // a bridge back from silence is news even when its state is the same (AC-30)
        let changed = t.state != body || !t.announced_alive;
        t.announced_alive = true;
        if changed {
            if let Some(w) = body["window"].as_str() {
                t.windows.insert(w.to_string(), (body.clone(), Instant::now()));
            }
            t.state = body;
            t.version += 1;
        }
        changed
    };
    if let (Some(key), Some(cwd)) = (key, cwd) {
        app.store.set_root(&key, &cwd);
    }
    if changed {
        app.refresh_watch();
        let t = app.term.lock();
        let s = state_of(&t);
        tracing::info!(event = "state", key = %s["key"], mode = %s["mode"], cwd = %s["cwd"]);
        app.bus.send(Event::State(s));
    }
    StatusCode::NO_CONTENT
}

#[derive(Deserialize)]
pub struct EventsQuery {
    pub(crate) client: Option<String>,
}

/// A panel's event stream; when it names its `client`, the panel's window claim (AC-36)
/// lasts as long as the stream.
pub async fn events(State(app): State<Shared>, Query(q): Query<EventsQuery>) -> Sse<impl Stream<Item = Result<SseEvent, std::io::Error>>> {
    let first = SseEvent::default().event("state").data(Event::State(state_json(&app)).data());
    let guard = q.client.map(|c| crate::panels::StreamGuard::new(app.clone(), c));
    let count = crate::socket::Counted::sse(&app);
    let rx = BroadcastStream::new(app.bus.subscribe()).map(move |e| {
        let _ = (&guard, &count);
        stream_event(e)
    });
    Sse::new(tokio_stream::once(Ok(first)).chain(rx)).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

fn stream_event(e: Result<Event, tokio_stream::wrappers::errors::BroadcastStreamRecvError>) -> Result<SseEvent, std::io::Error> {
    e.map(|e| SseEvent::default().event(e.name()).data(e.data()))
        .map_err(|e| std::io::Error::other(format!("event stream lost changes: {e}")))
}

#[derive(Deserialize)]
pub struct KeyQuery {
    key: String,
}

pub async fn get_workspace(State(app): State<Shared>, Query(q): Query<KeyQuery>) -> Json<Pane> {
    let cwd = {
        let t = app.term.lock();
        (t.state["key"].as_str() == Some(q.key.as_str())).then(|| t.state["cwd"].as_str().map(str::to_string)).flatten()
    };
    Json(app.store.get(&q.key, cwd.as_deref()))
}

/// `X-FB-Client` names the panel that asked: it ignores the echo of its own workspace
/// writes, and only it is told when the bridge fails its command.
pub(crate) fn client(headers: &HeaderMap) -> Option<String> {
    headers.get("x-fb-client").and_then(|v| v.to_str().ok()).map(str::to_string)
}

/// A command for the bridge, addressed back to the panel that asked (its errors go there).
pub(crate) fn command(app: &App, mut cmd: Value, headers: &HeaderMap) {
    cmd["by"] = json!(client(headers));
    let _ = app.commands.send(cmd);
}

pub(crate) fn no_bridge() -> Response {
    err(StatusCode::SERVICE_UNAVAILABLE, "no_bridge", "iTerm2 bridge not connected")
}

pub async fn open_quickly(State(app): State<Shared>, headers: HeaderMap) -> ApiResult {
    if !app.term.lock().bridge_alive() {
        return Err(no_bridge());
    }
    command(&app, json!({"action": "open-quickly"}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

pub async fn put_workspace(State(app): State<Shared>, Query(q): Query<KeyQuery>, headers: HeaderMap, Json(pane): Json<Pane>) -> Response {
    match app.store.put(&q.key, pane, client(&headers)) {
        Ok(rev) => {
            app.refresh_watch();
            Json(json!({"rev": rev})).into_response()
        }
        Err(cur) => (StatusCode::CONFLICT, Json(json!({"error": "stale_rev", "message": "Workspace changed; reload", "current": cur}))).into_response(),
    }
}

pub async fn get_prefs(State(app): State<Shared>) -> Json<Value> {
    Json(app.store.prefs())
}

pub async fn put_prefs(State(app): State<Shared>, Json(v): Json<Value>) -> StatusCode {
    app.store.set_prefs(v);
    StatusCode::NO_CONTENT
}

#[derive(Deserialize)]
pub struct TermBody {
    /// insert: paths typed (quoted) without Enter; cd: the folder
    #[serde(default)]
    paths: Vec<String>,
    path: Option<String>,
    /// the pane the panel shows; refused if focus moved elsewhere
    key: String,
}

pub async fn terminal(State(app): State<Shared>, UrlPath(action): UrlPath<String>, headers: HeaderMap, Json(b): Json<TermBody>) -> ApiResult {
    let (session, job) = {
        let t = app.term.lock();
        let Some(session) = t.state["session"].as_str().filter(|_| t.bridge_alive()).map(str::to_string) else {
            return Err(no_bridge());
        };
        if t.state["key"].as_str() != Some(b.key.as_str()) {
            return Err(err(StatusCode::CONFLICT, "focus_changed", "Focus moved to another terminal — command not sent"));
        }
        if t.state["busy"].as_bool().unwrap_or(false) {
            let job = t.state["job"].as_str().unwrap_or("");
            return Err(err(StatusCode::CONFLICT, "busy", format!("Terminal is busy ({job}) — command not sent")));
        }
        (session, t.state["job"].as_str().unwrap_or("").to_string())
    };
    // quoted for the pane's shell; never Enter: the user runs the line (AC-12)
    let text = termtext::line(&job, &action, &b.paths, b.path.as_deref()).map_err(|m| err(StatusCode::BAD_REQUEST, "not_typed", m))?;
    command(&app, json!({"action": "type", "session": session, "key": b.key, "job": job, "intent": action, "text": text}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize, serde::Serialize, Clone)]
pub struct ViewBody {
    path: String,
    /// a remote file's host (AC-37)
    host: Option<String>,
}

/// Open a file in the large viewer window (AC-26). The bridge creates the window with a
/// one-time code in its URL, or reuses the open one (then fbd relays `viewer-open`).
pub async fn view_open(State(app): State<Shared>, headers: HeaderMap, Json(b): Json<ViewBody>) -> ApiResult {
    crate::http::abs(&b.path)?;
    if !app.term.lock().bridge_alive() {
        return Err(no_bridge());
    }
    let code = app.tickets.issue();
    command(&app, json!({"action": "viewer", "path": b.path, "host": b.host, "code": code}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize)]
pub struct HostBody {
    host: String,
}

/// The panel's Enable / Not now / Remove for a remote host (AC-38): the bridge does it
/// (ssh lives there) and reports a failure to the panel that asked.
pub async fn remote_action(State(app): State<Shared>, UrlPath(action): UrlPath<String>, headers: HeaderMap, Json(b): Json<HostBody>) -> ApiResult {
    if !matches!(action.as_str(), "enable" | "dismiss" | "remove") || b.host.is_empty() || b.host.len() > 512 {
        return Err(err(StatusCode::BAD_REQUEST, "bad_request", "enable, dismiss or remove a host"));
    }
    if !app.term.lock().bridge_alive() {
        return Err(no_bridge());
    }
    command(&app, json!({"action": format!("host-{action}"), "host": b.host}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

/// The bridge reused the open viewer window: tell its page to add a tab.
pub async fn internal_viewer_open(State(app): State<Shared>, Json(b): Json<ViewBody>) -> StatusCode {
    let mut pending = app.viewer_pending.lock();
    pending.push(b.clone());
    let len = pending.len();
    pending.drain(..len.saturating_sub(20)); // a page that never connects must not grow this
    drop(pending);
    app.bus.send(Event::ViewerOpen { path: b.path, host: b.host });
    StatusCode::NO_CONTENT
}

#[derive(Deserialize)]
pub struct ErrorBody {
    message: String,
    by: Option<String>,
}

const ERROR_MAX: usize = 300;

/// A bridge command failed: tell the panel that asked (fail loud, AC-26).
pub async fn internal_error(State(app): State<Shared>, Json(b): Json<ErrorBody>) -> StatusCode {
    app.bus.send(bridge_error(&b.message, b.by));
    StatusCode::NO_CONTENT
}

fn bridge_error(message: &str, by: Option<String>) -> Event {
    let mut message: String = message.chars().filter(|c| !c.is_control()).take(ERROR_MAX).collect();
    if message.is_empty() {
        message = "iTerm2 bridge command failed".into();
    }
    Event::BridgeError { message, by }
}

/// The viewer page takes the files sent while it was still loading (then they are gone).
pub async fn view_pending(State(app): State<Shared>, headers: HeaderMap) -> Json<Vec<String>> {
    let host = headers.get("x-fb-host").and_then(|h| h.to_str().ok());
    Json(take_pending(&mut app.viewer_pending.lock(), host))
}

fn take_pending(pending: &mut Vec<ViewBody>, host: Option<&str>) -> Vec<String> {
    let mut own = Vec::new();
    pending.retain(|b| {
        if b.host.as_deref() == host { own.push(b.path.clone()); false } else { true }
    });
    own
}

/// A panel's Toolbelt was resized by the user: make it the default for new windows (AC-27).
pub async fn toolbelt_width(State(app): State<Shared>, headers: HeaderMap) -> StatusCode {
    command(&app, json!({"action": "default-width"}), &headers);
    StatusCode::NO_CONTENT
}

#[derive(Deserialize)]
pub struct TicketQuery {
    v: String,
}

/// Exchange a one-time viewer code for the token (no token needed; the code is the secret).
pub async fn ticket(State(app): State<Shared>, Query(q): Query<TicketQuery>) -> ApiResult {
    if app.tickets.redeem(&q.v) {
        Ok(Json(json!({"token": app.cfg.token})).into_response())
    } else {
        Err(err(StatusCode::UNAUTHORIZED, "bad_ticket", "This viewer link was already used or expired"))
    }
}

pub async fn internal_commands(State(app): State<Shared>) -> Sse<impl Stream<Item = Result<SseEvent, Infallible>>> {
    let rx = BroadcastStream::new(app.commands.subscribe()).filter_map(|c| c.ok().map(|c| Ok(SseEvent::default().event("command").data(c.to_string()))));
    Sse::new(rx).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

#[derive(Deserialize)]
pub struct WatchBody {
    root: String,
    #[serde(default)]
    expanded: Vec<String>,
    #[serde(default)]
    files: Vec<String>,
}

/// An agent watches what the Mac shows of its host (AC-37): the Mac's fbd sends it the
/// root, expanded folders and open files of the remote workspace on screen.
pub async fn watch(State(app): State<Shared>, Json(b): Json<WatchBody>) -> ApiResult {
    if !app.cfg.agent {
        return Err(err(StatusCode::NOT_FOUND, "not_found", "only an agent takes a watch list"));
    }
    if let Some(w) = &app.watcher {
        w.set(crate::watcher::wanted(&b.root, &b.expanded, b.files.into_iter()));
    }
    Ok(StatusCode::NO_CONTENT.into_response())
}

pub async fn health(State(app): State<Shared>) -> Json<Value> {
    let (cache_bytes, cache_dirs) = app.cache.stats();
    Json(json!({
        "uptime_s": app.started.elapsed().as_secs(),
        "build": crate::build_id(),
        "agent_id": crate::agent::AGENT_ID,
        "bridge_build": *app.bridge_build.lock(),
        "bridge_connected": app.term.lock().bridge_alive(),
        "cache_bytes": cache_bytes,
        "cache_dirs": cache_dirs,
        "sse_clients": app.streams.sse.load(Ordering::Relaxed),
        "ws_clients": app.streams.ws.load(Ordering::Relaxed),
        "windows": app.live.lock().window_count(),
        "workspaces": app.store.len(),
        "watched_dirs": app.watcher.as_ref().map_or(0, |w| w.len()),
        "writable_roots": app.roots.list(),
        "panels": app.panels.per_window(),
        "remotes": crate::remote::health(&app),
        "denied_requests": app.denied.load(Ordering::Relaxed),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn a_lagged_file_stream_errors_instead_of_silently_skipping_changes() {
        let bus = crate::events::Bus::new();
        let mut stream = BroadcastStream::new(bus.subscribe()).map(stream_event);
        for _ in 0..300 { bus.send(Event::Rescan { host: None }); }
        assert!(stream.next().await.unwrap().is_err());
    }

    #[test]
    fn pending_viewers_keep_their_host() {
        let mut pending = vec![ViewBody { path: "/same".into(), host: Some("devbox".into()) },
            ViewBody { path: "/same".into(), host: None }];
        assert_eq!(serde_json::to_value(take_pending(&mut pending, None)).unwrap(), json!(["/same"]), "old viewers still receive string paths");
        assert_eq!(pending.len(), 1);
        assert_eq!(take_pending(&mut pending, Some("devbox")), vec!["/same"]);
        assert!(pending.is_empty());
    }

    #[tokio::test]
    async fn missing_windows_do_not_borrow_focus_and_navigation_is_addressed() {
        let path = std::env::temp_dir().join(format!("fbd-state-query-{}.json", std::process::id()));
        let app = crate::tests::app(path);
        let mut commands = app.commands.subscribe();
        let mut headers = HeaderMap::new();
        headers.insert("x-fb-client", "panel".parse().unwrap());
        assert_eq!(open_quickly(State(app.clone()), headers.clone()).await.unwrap_err().status(), StatusCode::SERVICE_UNAVAILABLE);
        internal_state(State(app.clone()), Json(json!({"window":"w2","session":"p2","key":"p2"}))).await;
        let Json(s) = get_state(State(app.clone()), Query(StateQuery { window: Some("w1".into()) })).await;
        assert_eq!(s["window"], "w1");
        assert!(s["key"].is_null());
        assert_eq!(open_quickly(State(app), headers).await.unwrap().status(), StatusCode::NO_CONTENT);
        assert_eq!(commands.recv().await.unwrap(), json!({"action":"open-quickly","by":"panel"}));
    }

    #[test]
    fn bridge_errors_are_capped_and_addressed() {
        let e = bridge_error(&format!("bad\n{}", "x".repeat(400)), Some("p1".into()));
        assert_eq!(e.name(), "bridge-error");
        let v: Value = serde_json::from_str(&e.data()).unwrap();
        assert_eq!(v["by"], "p1");
        let m = v["message"].as_str().unwrap();
        assert!(m.starts_with("badx") && m.chars().count() == ERROR_MAX, "controls dropped, capped");
        let v: Value = serde_json::from_str(&bridge_error("", None).data()).unwrap();
        assert_eq!(v["message"], "iTerm2 bridge command failed");
        assert!(v["by"].is_null());
    }
}
