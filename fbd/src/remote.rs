// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Remote hosts (AC-37): file requests that name a host (`X-FB-Host`, or `host=` for the
//! GETs an <img> makes) go to that host's agent through the Unix socket ssh forwards for
//! it, unchanged; nothing of such a request touches a local file. The agent's file-change
//! events come back tagged with the host. Agents are not trusted: bodies are capped.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::Response;
use axum::Json;
use http_body_util::{BodyExt, Limited};
use hyper_util::rt::TokioIo;
use parking_lot::Mutex;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::events::{Event, Moved, Stamp};
use crate::http::err;
use crate::Shared;

const BODY_MAX: usize = 64 << 20;
/// Endpoints a remote host serves; everything else stays on the Mac.
const FORWARDED: [&str; 7] = ["/api/ls", "/api/file", "/api/raw", "/api/fs/mkdir", "/api/fs/touch", "/api/fs/rename", "/api/fs/trash"];

#[derive(Clone)]
struct Agent {
    socket: PathBuf,
    token: String,
    generation: u64,
}

#[derive(Default)]
pub struct Remotes {
    agents: Mutex<HashMap<String, Agent>>,
    generation: Mutex<u64>,
}

impl Remotes {
    fn get(&self, host: &str) -> Option<Agent> {
        self.agents.lock().get(host).cloned()
    }

    pub fn hosts(&self) -> Vec<String> {
        self.agents.lock().keys().cloned().collect()
    }
}

/// The host a request names (`Ok(None)`: none). A header or parameter that is present but
/// unreadable is an error, never "no host": that would fall through to local files.
fn host_of(req: &Request) -> Result<Option<String>, ()> {
    if let Some(v) = req.headers().get("x-fb-host") {
        return v.to_str().map(|h| Some(h.to_string()).filter(|h| !h.is_empty())).map_err(|_| ());
    }
    let Some(raw) = req.uri().query().and_then(|q| q.split('&').find_map(|kv| kv.strip_prefix("host="))) else { return Ok(None) };
    let raw = raw.replace('+', " ");
    let host = percent_encoding::percent_decode_str(&raw).decode_utf8().map_err(|_| ())?.into_owned();
    Ok(Some(host).filter(|h| !h.is_empty()))
}

/// Middleware: a request for a remote host never reaches a local handler.
pub async fn route(State(app): State<Shared>, req: Request, next: Next) -> Response {
    let host = match host_of(&req) {
        Ok(Some(h)) => h,
        Ok(None) => return next.run(req).await,
        Err(()) => return err(StatusCode::BAD_REQUEST, "bad_host", "The remote host name is not readable"),
    };
    let path = req.uri().path();
    if path.starts_with("/api/os/") {
        return err(StatusCode::BAD_REQUEST, "remote", "Not available for remote files");
    }
    if !FORWARDED.contains(&path) {
        return next.run(req).await; // workspace, prefs, terminal …: the Mac's own
    }
    let Some(agent) = app.remotes.get(&host) else {
        return err(StatusCode::NOT_FOUND, "no_agent", format!("{host} is not connected"));
    };
    forward(&host, &agent, req).await.unwrap_or_else(|e| {
        tracing::warn!(event = "remote.error", host, error = %e);
        err(StatusCode::BAD_GATEWAY, "unreachable", format!("{host} not reachable — try again"))
    })
}

async fn send(agent: &Agent, req: hyper::Request<Body>) -> Result<hyper::Response<hyper::body::Incoming>, String> {
    let stream = tokio::net::UnixStream::connect(&agent.socket).await.map_err(|e| e.to_string())?;
    let (mut sender, conn) = hyper::client::conn::http1::handshake(TokioIo::new(stream)).await.map_err(|e| e.to_string())?;
    tokio::spawn(conn);
    sender.send_request(req).await.map_err(|e| e.to_string())
}

/// The same request to the agent: its token and Host, no Origin, no local token in the
/// query, the body capped; the answer streams back capped.
async fn forward(host: &str, agent: &Agent, req: Request) -> Result<Response, String> {
    let (parts, body) = req.into_parts();
    let query: Vec<&str> = parts.uri.query().unwrap_or("").split('&').filter(|kv| !kv.is_empty() && !kv.starts_with("t=") && !kv.starts_with("host=")).collect();
    let uri = if query.is_empty() { parts.uri.path().to_string() } else { format!("{}?{}", parts.uri.path(), query.join("&")) };
    let body = Limited::new(body, BODY_MAX).collect().await.map_err(|e| e.to_string())?.to_bytes();
    let mut out = hyper::Request::builder().method(parts.method.clone()).uri(uri);
    for name in [header::CONTENT_TYPE, header::IF_MATCH, header::IF_NONE_MATCH] {
        if let Some(v) = parts.headers.get(&name) {
            out = out.header(name, v);
        }
    }
    let out = out
        .header(header::HOST, crate::agent::HOST)
        .header("x-fb-token", &agent.token)
        .body(Body::from(body))
        .map_err(|e| e.to_string())?;
    let res = send(agent, out).await?;
    tracing::debug!(event = "remote.forward", host, status = res.status().as_u16());
    let (parts, incoming) = res.into_parts();
    let mut response = Response::new(Body::new(Limited::new(incoming, BODY_MAX).map_err(axum::Error::new)));
    *response.status_mut() = parts.status;
    for name in [header::CONTENT_TYPE, header::ETAG, header::CONTENT_SECURITY_POLICY] {
        if let Some(v) = parts.headers.get(&name) {
            response.headers_mut().insert(name, v.clone());
        }
    }
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(uri: &str, header: Option<&[u8]>) -> Request {
        let mut r = axum::http::Request::builder().uri(uri);
        if let Some(h) = header {
            r = r.header("x-fb-host", HeaderValue::from_bytes(h).unwrap());
        }
        r.body(Body::empty()).unwrap()
    }

    #[test]
    fn hosts_are_read_exactly_or_refused() {
        assert_eq!(host_of(&req("/api/ls?path=/a", None)), Ok(None));
        assert_eq!(host_of(&req("/api/ls", Some(b"-p 2222 alex@vm"))), Ok(Some("-p 2222 alex@vm".into())));
        assert_eq!(host_of(&req("/api/raw?path=/a&host=-p%202222%20alex%40vm", None)), Ok(Some("-p 2222 alex@vm".into())));
        assert_eq!(host_of(&req("/api/raw?host=ai4&t=x", None)), Ok(Some("ai4".into())));
        assert_eq!(host_of(&req("/api/ls", Some(b"caf\xe9"))), Err(()), "unreadable: refused, not local");
    }
}

#[derive(Deserialize)]
pub struct RemoteBody {
    host: String,
    /// the forwarded socket on the Mac; null: the host is gone
    socket: Option<PathBuf>,
    token: Option<String>,
}

/// The bridge connected (or lost) a host's agent.
pub async fn internal_remote(State(app): State<Shared>, Json(b): Json<RemoteBody>) -> StatusCode {
    match (b.socket, b.token) {
        (Some(socket), Some(token)) => {
            let generation = {
                let mut g = app.remotes.generation.lock();
                *g += 1;
                *g
            };
            app.remotes.agents.lock().insert(b.host.clone(), Agent { socket, token, generation });
            tracing::info!(event = "remote.up", host = b.host);
            tokio::spawn(relay_events(app.clone(), b.host, generation));
            app.refresh_watch();
        }
        _ => {
            app.remotes.agents.lock().remove(&b.host);
            tracing::info!(event = "remote.down", host = b.host);
        }
    }
    StatusCode::NO_CONTENT
}

/// Republish the agent's file changes with the host, while this registration lasts.
async fn relay_events(app: Shared, host: String, generation: u64) {
    loop {
        let Some(agent) = app.remotes.get(&host).filter(|a| a.generation == generation) else { return };
        let req = hyper::Request::builder()
            .uri("/api/events")
            .header(header::HOST, crate::agent::HOST)
            .header("x-fb-token", &agent.token)
            .body(Body::empty())
            .expect("static request");
        if let Ok(res) = send(&agent, req).await {
            let mut body = res.into_body();
            let (mut buf, mut event) = (String::new(), String::new());
            while let Some(Ok(frame)) = body.frame().await {
                let Some(data) = frame.data_ref() else { continue };
                buf.push_str(&String::from_utf8_lossy(data));
                if buf.len() > 1 << 20 {
                    break; // an agent that never ends a line is not trusted further: reconnect
                }
                while let Some(i) = buf.find('\n') {
                    let line: String = buf.drain(..=i).collect();
                    let line = line.trim_end();
                    if let Some(e) = line.strip_prefix("event:") {
                        event = e.trim().to_string();
                    } else if let (Some(d), "fs-change") = (line.strip_prefix("data:"), event.as_str()) {
                        if let Some(e) = fs_change(&host, d) {
                            app.bus.send(e);
                        }
                    }
                }
                if app.remotes.get(&host).is_none_or(|a| a.generation != generation) {
                    return;
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

fn fs_change(host: &str, data: &str) -> Option<Event> {
    let v: Value = serde_json::from_str(data).ok()?;
    let strings = |k: &str| v[k].as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()).unwrap_or_default();
    let files: Vec<Stamp> = serde_json::from_value(v["files"].clone()).unwrap_or_default();
    let moved: Vec<Moved> = serde_json::from_value(v["moved"].clone()).unwrap_or_default();
    Some(Event::FsChange { dirs: strings("dirs"), files, moved, host: Some(host.to_string()) })
}

/// Tell `host`'s agent what the Mac shows of it, so it watches those folders.
pub fn watch(remotes: &Remotes, host: &str, root: String, expanded: Vec<String>, files: Vec<String>) {
    let Some(agent) = remotes.get(host) else { return };
    tokio::spawn(async move {
        let body = json!({"root": root, "expanded": expanded, "files": files}).to_string();
        let req = hyper::Request::builder()
            .method(Method::PUT)
            .uri("/api/watch")
            .header(header::HOST, crate::agent::HOST)
            .header(header::CONTENT_TYPE, HeaderValue::from_static("application/json"))
            .header("x-fb-token", &agent.token)
            .body(Body::from(body))
            .expect("static request");
        let _ = send(&agent, req).await;
    });
}

pub fn health(app: &Shared) -> Value {
    json!(app.remotes.hosts())
}
