// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Response helpers shared by the handlers, and the embedded UI.

use std::path::PathBuf;

use axum::body::Body;
use axum::extract::Request;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::ops::OpError;

/// Handlers return `Err(response)` for early exits, so `?` replaces match boilerplate.
pub type ApiResult = Result<Response, Response>;

pub fn err(status: StatusCode, code: &str, message: impl Into<String>) -> Response {
    (status, Json(json!({"error": code, "message": message.into()}))).into_response()
}

pub fn op_err(e: OpError) -> Response {
    let status = match &e {
        OpError::BadName(_) => StatusCode::BAD_REQUEST,
        OpError::Denied(_) => StatusCode::FORBIDDEN,
        OpError::Missing(_) => StatusCode::NOT_FOUND,
        OpError::Exists(_) | OpError::Conflict { .. } => StatusCode::CONFLICT,
        OpError::Io(_) => StatusCode::INTERNAL_SERVER_ERROR,
    };
    let mut body = json!({"error": e.code(), "message": e.message()});
    if let OpError::Conflict { etag, .. } = &e {
        body["etag"] = json!(etag);
    }
    (status, Json(body)).into_response()
}

/// File-system work runs off the async workers; a panic in it answers 500.
pub async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> Result<T, Response> {
    tokio::task::spawn_blocking(f).await.map_err(|e| err(StatusCode::INTERNAL_SERVER_ERROR, "internal", e.to_string()))
}

/// An absolute path without `..` components.
pub fn abs(path: &str) -> Result<PathBuf, Response> {
    let p = PathBuf::from(path);
    if !p.is_absolute() || p.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
        return Err(err(StatusCode::BAD_REQUEST, "bad_path", "Path must be absolute without '..'"));
    }
    Ok(p)
}

#[derive(rust_embed::Embed)]
#[folder = "../ui/dist"]
struct Ui;

/// The panel's files; `index.html` carries the CSP that keeps file content inert.
pub async fn ui(req: Request) -> Response {
    let path = req.uri().path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    let Some(file) = Ui::get(path) else {
        return err(StatusCode::NOT_FOUND, "not_found", format!("{path} not found"));
    };
    let mime = mime_guess::from_path(path).first_or_octet_stream();
    let mut res = Response::new(Body::from(file.data.into_owned()));
    res.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_str(mime.as_ref()).unwrap());
    if path == "index.html" {
        // the event socket by name: WebKit's 'self' may not cover ws: (AC-41); the guard
        // admitted only our exact Host
        let host = req.headers().get(header::HOST).and_then(|h| h.to_str().ok()).unwrap_or_default();
        let csp = format!("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'; connect-src 'self' ws://{host}; frame-ancestors 'none'");
        res.headers_mut().insert("content-security-policy", HeaderValue::from_str(&csp).unwrap_or(HeaderValue::from_static("default-src 'self'")));
    }
    res
}
