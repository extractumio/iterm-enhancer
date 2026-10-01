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
use crate::workspace::Pane;
use crate::{App, Shared, Term};

fn state_json(app: &App) -> Value {
    state_of(&app.term.lock())
}

/// The state panels see. Send it while holding `term`, so events leave in the order the
/// state changed (a bridge's return must not be overtaken by its silence).
pub fn state_of(t: &Term) -> Value {
    let mut s = if t.state.is_object() { t.state.clone() } else { json!({}) };
    s["version"] = json!(t.version);
    s["bridge"] = json!(t.bridge_alive());
    s
}

pub async fn get_state(State(app): State<Shared>) -> Json<Value> {
    Json(state_json(&app))
}

pub async fn internal_state(State(app): State<Shared>, Json(mut body): Json<Value>) -> StatusCode {
    body.as_object_mut().map(|o| o.remove("version"));
    let (key, cwd) = (body["key"].as_str().map(str::to_string), body["cwd"].as_str().map(str::to_string));
    let changed = {
        let mut t = app.term.lock();
        t.last_push = Some(Instant::now());
        // a bridge back from silence is news even when its state is the same (AC-30)
        let changed = t.state != body || !t.announced_alive;
        t.announced_alive = true;
        if changed {
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

pub async fn events(State(app): State<Shared>) -> Sse<impl Stream<Item = Result<SseEvent, Infallible>>> {
    let first = SseEvent::default().event("state").data(Event::State(state_json(&app)).data());
    let rx = BroadcastStream::new(app.bus.subscribe()).filter_map(|e| e.ok().map(|e| Ok(SseEvent::default().event(e.name()).data(e.data()))));
    Sse::new(tokio_stream::once(Ok(first)).chain(rx)).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
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
fn client(headers: &HeaderMap) -> Option<String> {
    headers.get("x-fb-client").and_then(|v| v.to_str().ok()).map(str::to_string)
}

/// A command for the bridge, addressed back to the panel that asked (its errors go there).
fn command(app: &App, mut cmd: Value, headers: &HeaderMap) {
    cmd["by"] = json!(client(headers));
    let _ = app.commands.send(cmd);
}

fn no_bridge() -> Response {
    err(StatusCode::SERVICE_UNAVAILABLE, "no_bridge", "iTerm2 bridge not connected")
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

/// C0 controls and DEL would act as keystrokes (Ctrl-C, Enter…) in the terminal.
fn has_control(s: &str) -> bool {
    s.chars().any(|c| (c as u32) < 0x20 || c as u32 == 0x7f)
}

/// POSIX shell word: bare when safe, single-quoted otherwise.
fn shell_quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "'\\''"))
    }
}

pub async fn terminal(State(app): State<Shared>, UrlPath(action): UrlPath<String>, headers: HeaderMap, Json(b): Json<TermBody>) -> ApiResult {
    if b.paths.iter().chain(&b.path).any(|s| has_control(s)) {
        return Err(err(StatusCode::BAD_REQUEST, "control_chars", "Name contains control characters — not sent to the terminal"));
    }
    let session = {
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
        session
    };
    let text = match (action.as_str(), b.path) {
        ("insert", _) if !b.paths.is_empty() => b.paths.iter().map(|p| shell_quote(p)).collect::<Vec<_>>().join(" ") + " ",
        ("cd", Some(p)) => format!("cd -- {}\r", shell_quote(&p)),
        _ => return Err(err(StatusCode::BAD_REQUEST, "bad_request", "insert needs paths, cd needs path")),
    };
    command(&app, json!({"action": "type", "session": session, "text": text}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize)]
pub struct ViewBody {
    path: String,
}

/// Open a file in the large viewer window (AC-26). The bridge creates the window with a
/// one-time code in its URL, or reuses the open one (then fbd relays `viewer-open`).
pub async fn view_open(State(app): State<Shared>, headers: HeaderMap, Json(b): Json<ViewBody>) -> ApiResult {
    crate::http::abs(&b.path)?;
    if !app.term.lock().bridge_alive() {
        return Err(no_bridge());
    }
    let code = app.tickets.issue();
    command(&app, json!({"action": "viewer", "path": b.path, "code": code}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

/// The bridge reused the open viewer window: tell its page to add a tab.
pub async fn internal_viewer_open(State(app): State<Shared>, Json(b): Json<ViewBody>) -> StatusCode {
    let mut pending = app.viewer_pending.lock();
    pending.push(b.path.clone());
    let len = pending.len();
    pending.drain(..len.saturating_sub(20)); // a page that never connects must not grow this
    drop(pending);
    app.bus.send(Event::ViewerOpen { path: b.path });
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
pub async fn view_pending(State(app): State<Shared>) -> Json<Vec<String>> {
    Json(std::mem::take(&mut *app.viewer_pending.lock()))
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

pub async fn health(State(app): State<Shared>) -> Json<Value> {
    let (cache_bytes, cache_dirs) = app.cache.stats();
    Json(json!({
        "uptime_s": app.started.elapsed().as_secs(),
        "bridge_connected": app.term.lock().bridge_alive(),
        "cache_bytes": cache_bytes,
        "cache_dirs": cache_dirs,
        "sse_clients": app.bus.subscribers(),
        "workspaces": app.store.len(),
        "watched_dirs": app.watcher.as_ref().map_or(0, |w| w.len()),
        "writable_roots": app.roots.list(),
        "denied_requests": app.denied.load(Ordering::Relaxed),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoting_and_controls() {
        assert_eq!(shell_quote("src/db/pool.rs"), "src/db/pool.rs");
        assert_eq!(shell_quote("my file's"), "'my file'\\''s'");
        assert!(has_control("a\u{3}b") && has_control("x\r") && has_control("\u{7f}"));
        assert!(!has_control("naïve file.txt"));
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
