// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Who may talk to fbd: the token for /api, the bridge secret for /internal, exact Host,
//! and same-origin JSON for writes. Also the app folder and the token file.

use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;

use axum::extract::{Request, State};
use axum::http::{header, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::Response;

use crate::http::err;
use crate::Shared;

/// `FB_APP_DIR`, default `~/Library/Application Support/iterm-filebrowser` (tests use their own).
pub fn app_dir() -> PathBuf {
    let dir = std::env::var_os("FB_APP_DIR").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var("HOME").expect("HOME")).join("Library/Application Support/iterm-filebrowser")
    });
    std::fs::create_dir_all(&dir).expect("create app dir");
    dir
}

/// `N` random bytes as lowercase hex.
pub fn random_hex<const N: usize>() -> String {
    rand::random::<[u8; N]>().iter().map(|b| format!("{b:02x}")).collect()
}

/// 128-bit token, created once, mode 0600, stable so the registered tool URL keeps working.
pub fn load_token(dir: &Path) -> String {
    use std::os::unix::fs::OpenOptionsExt;
    let path = dir.join("token");
    if let Ok(t) = std::fs::read_to_string(&path) {
        if t.trim().len() >= 16 {
            return t.trim().to_string();
        }
    }
    let token = random_hex::<16>();
    let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&path).expect("write token");
    std::io::Write::write_all(&mut f, token.as_bytes()).expect("write token");
    token
}

pub async fn guard(State(app): State<Shared>, req: Request, next: Next) -> Response {
    let deny = |status, code: &str, msg: String| {
        app.denied.fetch_add(1, Ordering::Relaxed);
        tracing::warn!(event = "auth.denied", reason = code, path = %req.uri().path());
        err(status, code, msg)
    };
    let h = req.headers();
    let header_str = |name: &str| h.get(name).and_then(|v| v.to_str().ok());
    if header_str(header::HOST.as_str()) != Some(app.cfg.host.as_str()) {
        return deny(StatusCode::FORBIDDEN, "bad_host", "Host is not allowed".into());
    }
    let path = req.uri().path();
    if path.starts_with("/api/") {
        // the query token exists for GETs a header cannot carry (EventSource, <img>)
        let query_token = req.uri().query().and_then(|q| q.split('&').find_map(|kv| kv.strip_prefix("t=")));
        let token = app.cfg.token.as_str();
        if header_str("x-fb-token") != Some(token) && !(req.method() == Method::GET && query_token == Some(token)) {
            return deny(StatusCode::UNAUTHORIZED, "bad_token", "Missing or wrong token".into());
        }
    } else if path.starts_with("/internal/")
        && !matches!((&app.cfg.bridge_secret, header_str("x-fb-bridge")), (Some(s), Some(given)) if s == given)
    {
        return deny(StatusCode::UNAUTHORIZED, "bad_bridge_secret", "Bridge secret required".into());
    }
    if !matches!(*req.method(), Method::GET | Method::HEAD) {
        if let Some(origin) = header_str(header::ORIGIN.as_str()) {
            if origin != format!("http://127.0.0.1:{}", app.cfg.port) {
                return deny(StatusCode::FORBIDDEN, "bad_origin", format!("Origin {origin} is not allowed"));
            }
        }
        if !header_str(header::CONTENT_TYPE.as_str()).is_some_and(|c| c.starts_with("application/json")) {
            return deny(StatusCode::UNSUPPORTED_MEDIA_TYPE, "bad_content_type", "Content-Type must be application/json".into());
        }
    }
    let mut res = next.run(req).await;
    let hs = res.headers_mut();
    hs.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    hs.insert("x-content-type-options", HeaderValue::from_static("nosniff"));
    hs.entry(header::CACHE_CONTROL).or_insert(HeaderValue::from_static("no-store"));
    res
}
