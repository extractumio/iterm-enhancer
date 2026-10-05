// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! The panel's event socket (AC-41): the events of `/api/events` over a WebSocket. WebKit
//! gives all of iTerm2's web views 6 HTTP/1.1 connections to fbd together, and an open SSE
//! stream holds one of them; WebSockets are not counted (AS-07), so 100 panels fit.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::Response;
use futures_util::stream::SplitSink;
use futures_util::{SinkExt, StreamExt};
use tokio::sync::broadcast::{error::RecvError, Receiver};

use crate::api_state::{state_json, EventsQuery};
use crate::events::Event;
use crate::http::err;
use crate::Shared;

/// Keeps the socket busy; a dead peer on loopback is reset by the kernel, so no pong is awaited.
const PING: Duration = Duration::from_secs(15);
/// A panel that stops reading without closing ends its socket (and its window claim).
const SEND: Duration = Duration::from_secs(10);
/// The panel sends nothing but control frames.
const MAX_MESSAGE: usize = 4096;
/// More SSE streams than this leave too few of WebKit's 6 connections.
const MANY_SSE: usize = 4;

/// Open event streams by transport (`/api/health`).
#[derive(Default)]
pub struct Streams {
    pub sse: AtomicUsize,
    pub ws: AtomicUsize,
}

/// One open stream, counted while it lives.
pub struct Counted(Shared, bool);

impl Counted {
    pub fn sse(app: &Shared) -> Self {
        let open = app.streams.sse.fetch_add(1, Ordering::Relaxed) + 1;
        if open > MANY_SSE {
            tracing::warn!(event = "events.many", open, "SSE streams hold most of WebKit's 6 connections to fbd");
        }
        Counted(app.clone(), false)
    }

    fn ws(app: &Shared) -> Self {
        app.streams.ws.fetch_add(1, Ordering::Relaxed);
        Counted(app.clone(), true)
    }
}

impl Drop for Counted {
    fn drop(&mut self) {
        let n = if self.1 { &self.0.streams.ws } else { &self.0.streams.sse };
        n.fetch_sub(1, Ordering::Relaxed);
    }
}

/// Only the panel page may open the socket. A WebSocket is a GET that any site's page can
/// start, and browsers always send its Origin, so a missing one is refused too.
pub fn origin_ok(headers: &HeaderMap, port: u16) -> bool {
    headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()) == Some(format!("http://127.0.0.1:{port}").as_str())
}

pub async fn socket(State(app): State<Shared>, Query(q): Query<EventsQuery>, headers: HeaderMap, ws: WebSocketUpgrade) -> Response {
    if !origin_ok(&headers, app.cfg.port) {
        app.denied.fetch_add(1, Ordering::Relaxed);
        tracing::warn!(event = "auth.denied", reason = "bad_origin", path = "/api/ws");
        return err(StatusCode::FORBIDDEN, "bad_origin", "Origin is not allowed");
    }
    let rx = app.bus.subscribe(); // before the first state, so nothing falls between them
    ws.max_message_size(MAX_MESSAGE).on_upgrade(move |s| serve(app, q.client, rx, s))
}

async fn serve(app: Shared, client: Option<String>, mut rx: Receiver<Event>, ws: WebSocket) {
    let _count = Counted::ws(&app);
    let _claim = client.map(|c| crate::panels::StreamGuard::new(app.clone(), c));
    let (mut tx, mut incoming) = ws.split();
    if !send(&mut tx, frame(&Event::State(state_json(&app)))).await {
        return;
    }
    if let Some(value) = crate::recovery::current(&app).await {
        if !send(&mut tx, frame(&Event::Recovery(value))).await { return; }
    }
    let mut ping = tokio::time::interval(PING);
    ping.tick().await;
    loop {
        tokio::select! {
            e = next(&mut rx) => {
                let Some(e) = e else {
                    // behind: the panel opens it again and starts from the current state
                    send(&mut tx, Message::Close(None)).await;
                    return;
                };
                if !send(&mut tx, frame(&e)).await { return }
            }
            _ = ping.tick() => if !send(&mut tx, Message::Ping(Default::default())).await { return },
            m = incoming.next() => if !matches!(m, Some(Ok(Message::Ping(_) | Message::Pong(_) | Message::Text(_) | Message::Binary(_)))) { return },
        }
    }
}

async fn send(tx: &mut SplitSink<WebSocket, Message>, m: Message) -> bool {
    matches!(tokio::time::timeout(SEND, tx.send(m)).await, Ok(Ok(())))
}

/// The next event, or `None` when this socket fell behind the bus (events were dropped:
/// the panel must not go on showing stale state) or the bus is gone.
async fn next(rx: &mut Receiver<Event>) -> Option<Event> {
    match rx.recv().await {
        Ok(e) => Some(e),
        Err(RecvError::Lagged(n)) => {
            tracing::warn!(event = "ws.lagged", missed = n);
            None
        }
        Err(RecvError::Closed) => None,
    }
}

/// `{"event": name, "data": …}`, with the same payload as the SSE event.
fn frame(e: &Event) -> Message {
    Message::Text(format!(r#"{{"event":"{}","data":{}}}"#, e.name(), e.data()).into())
}

/// Processes started by iTerm2 may open 256 files; up to 100 panels hold a socket each.
pub fn raise_open_files() {
    let mut r = libc::rlimit { rlim_cur: 0, rlim_max: 0 };
    if unsafe { libc::getrlimit(libc::RLIMIT_NOFILE, &mut r) } != 0 {
        return;
    }
    let want = r.rlim_max.min(8192);
    if r.rlim_cur < want {
        let raised = libc::rlimit { rlim_cur: want, rlim_max: r.rlim_max };
        if unsafe { libc::setrlimit(libc::RLIMIT_NOFILE, &raised) } != 0 {
            tracing::warn!(event = "nofile.unchanged", limit = r.rlim_cur);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;
    use serde_json::{json, Value};

    #[test]
    fn only_the_panel_origin_opens_the_socket() {
        let mut h = HeaderMap::new();
        assert!(!origin_ok(&h, 47821), "no Origin");
        h.insert(header::ORIGIN, HeaderValue::from_static("http://127.0.0.1:47821"));
        assert!(origin_ok(&h, 47821));
        assert!(!origin_ok(&h, 47832), "another port");
        for o in ["http://localhost:47821", "https://127.0.0.1:47821", "http://evil.example", "null"] {
            h.insert(header::ORIGIN, HeaderValue::from_static(o));
            assert!(!origin_ok(&h, 47821), "{o}");
        }
    }

    #[tokio::test]
    async fn a_socket_behind_the_bus_is_closed() {
        let bus = crate::events::Bus::new();
        let mut rx = bus.subscribe();
        bus.send(Event::Unbind { clients: vec!["a".into()] });
        assert!(next(&mut rx).await.is_some());
        for _ in 0..300 {
            bus.send(Event::Unbind { clients: vec![] });
        }
        assert!(next(&mut rx).await.is_none(), "lagged: close, the panel reconnects with the current state");
    }

    #[test]
    fn frames_carry_the_sse_payload() {
        let e = Event::Workspace { key: "k".into(), rev: 3, by: None };
        let Message::Text(t) = frame(&e) else { panic!("text frame") };
        let v: Value = serde_json::from_str(&t).unwrap();
        assert_eq!(v["event"], "workspace");
        assert_eq!(v["data"], serde_json::from_str::<Value>(&e.data()).unwrap());
        let Message::Text(t) = frame(&Event::State(json!({"cwd": "/Users/alex"}))) else { panic!() };
        assert_eq!(serde_json::from_str::<Value>(&t).unwrap()["data"]["cwd"], "/Users/alex");
    }
}
