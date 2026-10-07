// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Who may talk to fbd over TCP: the token for /api, exact Host, and same-origin JSON for
//! writes (the bridge uses its own socket, local.rs). Also the app folder and the token file.

use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;

use axum::extract::{Request, State};
use axum::http::{header, HeaderValue, Method, StatusCode};
use axum::middleware::Next;
use axum::response::Response;

use crate::http::err;
use crate::local::same;
use crate::Shared;

/// `FB_APP_DIR`, default `~/.iterm-enhancer/state` (tests use their own): private (0700)
/// and ours, or fbd does not start; what is in it is only for this user (AC-07).
pub fn app_dir() -> PathBuf {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let dir = std::env::var_os("FB_APP_DIR").map(PathBuf::from).unwrap_or_else(|| {
        PathBuf::from(std::env::var("HOME").expect("HOME")).join(".iterm-enhancer/state")
    });
    std::fs::create_dir_all(&dir).expect("create app dir");
    let meta = std::fs::metadata(&dir).expect("app dir");
    if meta.uid() != unsafe { libc::geteuid() } {
        eprintln!("error: {} belongs to another user", dir.display());
        std::process::exit(2);
    }
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)).expect("app dir mode");
    dir
}

/// `N` random bytes as lowercase hex.
pub fn random_hex<const N: usize>() -> String {
    rand::random::<[u8; N]>().iter().map(|b| format!("{b:02x}")).collect()
}

/// 128-bit token, mode 0600, kept so the registered tool URL keeps working, and replaced
/// (`renew`, or when there is none) once it may have reached another program (AC-07). fbd is
/// the only writer: a new one is written beside and renamed into place.
pub fn load_token(dir: &Path, renew: bool) -> String {
    use std::os::unix::fs::OpenOptionsExt;
    let path = dir.join("token");
    if !renew {
        if let Ok(t) = std::fs::read_to_string(&path) {
            if t.trim().len() >= 16 {
                return t.trim().to_string();
            }
        }
    }
    let token = random_hex::<16>();
    let tmp = dir.join(".token.new");
    let _ = std::fs::remove_file(&tmp);
    let mut f = std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(&tmp).expect("write token");
    std::io::Write::write_all(&mut f, token.as_bytes()).expect("write token");
    std::fs::rename(&tmp, &path).expect("write token");
    token
}

/// The token may have reached whoever holds the port: the next fbd makes a new one.
pub fn forget_token(dir: &Path) {
    let _ = std::fs::remove_file(dir.join("token"));
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
    // /api/hello is how the panel checks it talks to fbd before it sends the token (AC-07);
    // /internal/* is not served here at all, only on the bridge's socket
    if path.starts_with("/api/") && path != "/api/hello" {
        // the query token exists for GETs a header cannot carry (EventSource, WebSocket, <img>)
        let query_token = req.uri().query().and_then(|q| q.split('&').find_map(|kv| kv.strip_prefix("t=")));
        let token = app.cfg.token.as_str();
        let ok = |t: Option<&str>| t.is_some_and(|t| same(t, token));
        if !ok(header_str("x-fb-token")) && !(req.method() == Method::GET && ok(query_token)) {
            return deny(StatusCode::UNAUTHORIZED, "bad_token", "Missing or wrong token".into());
        }
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
