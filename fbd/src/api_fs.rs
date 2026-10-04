// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! File-system endpoints: listings, reading, saving, create/rename/trash, macOS open.

use std::path::{Path, PathBuf};

use axum::body::Body;
use axum::extract::rejection::JsonRejection;
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::http::{abs, blocking, err, op_err, ApiResult};
use crate::ops::{self, OpError};
use crate::{files, listing, Shared};

#[derive(Deserialize)]
pub struct LsQuery {
    path: String,
    #[serde(default)]
    offset: usize,
    #[serde(default = "page_max")]
    limit: usize,
    #[serde(default)]
    filter: String,
    #[serde(default = "yes")]
    hidden: bool,
    locate: Option<String>,
}
fn page_max() -> usize {
    listing::PAGE_MAX
}
fn yes() -> bool {
    true
}

pub async fn ls(State(app): State<Shared>, Query(q): Query<LsQuery>) -> ApiResult {
    let path = abs(&q.path)?;
    let mut page = app.cache.page(&path, q.offset, q.limit, &q.filter, q.hidden, q.locate.as_deref()).await;
    page.writable = blocking(move || app.roots.allows(&path)).await?;
    Ok(Json(page).into_response())
}

#[derive(Deserialize)]
pub struct PathQuery {
    path: String,
}

/// Text and metadata of a file; `If-None-Match: <etag>` answers 304 when unchanged.
pub async fn file(State(app): State<Shared>, Query(q): Query<PathQuery>, headers: HeaderMap) -> ApiResult {
    let path = abs(&q.path)?;
    if let Some(tag) = headers.get(header::IF_NONE_MATCH).and_then(|v| v.to_str().ok()) {
        let now = tokio::fs::metadata(&path).await.map_err(|e| op_err(ops::io(e, &path)))?;
        if tag.trim_matches('"') == files::etag(&now) {
            return Ok(StatusCode::NOT_MODIFIED.into_response());
        }
    }
    let v = blocking(move || files::read_view(&path, app.cfg.text_max, &app.roots)).await?.map_err(op_err)?;
    Ok(Json(v).into_response())
}

const RAW_MAX: u64 = 64 << 20;

/// Raw bytes for images; an SVG opened directly never runs script.
pub async fn raw(Query(q): Query<PathQuery>) -> ApiResult {
    let path = abs(&q.path)?;
    let p = path.clone();
    let bytes = blocking(move || -> Result<Vec<u8>, OpError> {
        use std::io::Read;
        let (f, _) = files::open_regular(&p)?;
        let mut buf = Vec::new();
        f.take(RAW_MAX + 1).read_to_end(&mut buf).map_err(|e| ops::io(e, &p))?;
        Ok(buf)
    })
    .await?
    .map_err(op_err)?;
    if bytes.len() as u64 > RAW_MAX {
        return Err(err(StatusCode::BAD_REQUEST, "too_large", "Larger than 64 MB"));
    }
    let mime = mime_guess::from_path(&path).first_or_octet_stream();
    let mut res = Response::new(Body::from(bytes));
    let h = res.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_str(mime.as_ref()).unwrap());
    h.insert("content-security-policy", HeaderValue::from_static("default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"));
    Ok(res)
}

#[derive(Deserialize)]
pub struct SaveBody {
    text: String,
}

/// Room for a text of the text limit as JSON: escaping can make it up to 6 times larger.
pub fn save_body_limit(text_max: u64) -> usize {
    (text_max as usize).saturating_mul(6) + (64 << 10)
}

pub async fn save_file(State(app): State<Shared>, Query(q): Query<PathQuery>, headers: HeaderMap, body: Result<Json<SaveBody>, JsonRejection>) -> ApiResult {
    let path = abs(&q.path)?;
    // a body over the limit is refused as JSON like every other error (AC-08)
    let Json(b) = body.map_err(|e| err(e.status(), "bad_body", e.body_text()))?;
    if b.text.len() as u64 > app.cfg.text_max {
        return Err(err(StatusCode::PAYLOAD_TOO_LARGE, "too_large", "Larger than the text limit — not saved"));
    }
    let if_match = headers
        .get(header::IF_MATCH)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.trim_matches('"').to_string())
        .ok_or_else(|| err(StatusCode::PRECONDITION_REQUIRED, "if_match_required", "If-Match header required"))?;
    let (app2, p2) = (app.clone(), path.clone());
    match blocking(move || ops::save(&app2.roots, &p2, &b.text, &if_match)).await? {
        Ok(etag) => {
            tracing::info!(event = "save", path = %path.display());
            app.changed(&parents(&[&path]), &[path], &[]);
            Ok(Json(json!({"etag": etag})).into_response())
        }
        Err(e) => {
            if matches!(e, OpError::Conflict { .. }) {
                tracing::warn!(event = "save.conflict", path = %path.display());
            }
            Err(op_err(e))
        }
    }
}

fn parents<P: AsRef<Path>>(paths: &[P]) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = paths.iter().filter_map(|p| p.as_ref().parent().map(Path::to_path_buf)).collect();
    v.dedup();
    v
}

#[derive(Deserialize)]
pub struct CreateBody {
    parent: String,
    name: String,
}

async fn create(app: Shared, b: CreateBody, dir: bool) -> ApiResult {
    let parent = abs(&b.parent)?;
    let (app2, at) = (app.clone(), parent.clone());
    let p = blocking(move || if dir { ops::mkdir(&app2.roots, &at, &b.name) } else { ops::touch(&app2.roots, &at, &b.name) })
        .await?
        .map_err(op_err)?;
    tracing::info!(event = if dir { "mkdir" } else { "touch" }, path = %p.display());
    app.changed(&[parent], &[p.clone()], &[]);
    Ok((StatusCode::CREATED, Json(json!({"path": p.display().to_string()}))).into_response())
}

pub async fn fs_mkdir(State(app): State<Shared>, Json(b): Json<CreateBody>) -> ApiResult {
    create(app, b, true).await
}

pub async fn fs_touch(State(app): State<Shared>, Json(b): Json<CreateBody>) -> ApiResult {
    create(app, b, false).await
}

#[derive(Deserialize)]
pub struct RenameBody {
    path: String,
    name: String,
}

pub async fn fs_rename(State(app): State<Shared>, Json(b): Json<RenameBody>) -> ApiResult {
    let path = abs(&b.path)?;
    let (app2, from) = (app.clone(), path.clone());
    let to = blocking(move || ops::rename(&app2.roots, &from, &b.name)).await?.map_err(op_err)?;
    tracing::info!(event = "rename", from = %path.display(), to = %to.display());
    app.changed(&parents(&[&path]), &[], &[(path, to.clone())]);
    Ok(Json(json!({"path": to.display().to_string()})).into_response())
}

#[derive(Deserialize)]
pub struct TrashBody {
    paths: Vec<String>,
}

pub async fn fs_trash(State(app): State<Shared>, Json(b): Json<TrashBody>) -> ApiResult {
    let paths = b.paths.iter().map(|p| abs(p)).collect::<Result<Vec<_>, _>>()?;
    let (app2, list) = (app.clone(), paths.clone());
    let errors = blocking(move || ops::trash(&app2.roots, &list)).await?;
    app.changed(&parents(&paths), &paths, &[]);
    tracing::info!(event = "trash", count = paths.len(), failed = errors.len());
    let failed: Vec<Value> = errors.iter().map(|(p, e)| json!({"path": p.display().to_string(), "error": e.code(), "message": e.message()})).collect();
    let status = match failed.len() {
        0 => StatusCode::OK,
        n if n == paths.len() => StatusCode::CONFLICT,
        _ => StatusCode::MULTI_STATUS,
    };
    Ok((status, Json(json!({"trashed": paths.len() - failed.len(), "failed": failed}))).into_response())
}

#[derive(Deserialize)]
pub struct OpenBody {
    path: Option<String>,
    url: Option<String>,
}

/// Web links and files open in their default macOS app; the panel itself never navigates.
pub async fn os_open(Json(b): Json<OpenBody>) -> ApiResult {
    let target = match (b.url, b.path) {
        (Some(u), _) if ["http://", "https://", "mailto:"].iter().any(|p| u.starts_with(p)) => u,
        (Some(u), _) => return Err(err(StatusCode::BAD_REQUEST, "bad_url", format!("Only http, https and mailto links open: {u}"))),
        (None, Some(p)) => existing(&p).await?.display().to_string(),
        _ => return Err(err(StatusCode::BAD_REQUEST, "bad_request", "path or url required")),
    };
    run_open(&["--", &target]).await
}

pub async fn os_reveal(Json(b): Json<OpenBody>) -> ApiResult {
    let p = existing(b.path.as_deref().unwrap_or("")).await?;
    run_open(&["-R", "--", &p.to_string_lossy()]).await
}

async fn existing(path: &str) -> Result<PathBuf, Response> {
    let p = abs(path)?;
    tokio::fs::symlink_metadata(&p).await.map_err(|e| op_err(ops::io(e, &p)))?;
    Ok(p)
}

async fn run_open(args: &[&str]) -> ApiResult {
    match tokio::process::Command::new("/usr/bin/open").args(args).status().await {
        Ok(st) if st.success() => Ok(StatusCode::NO_CONTENT.into_response()),
        Ok(st) => Err(err(StatusCode::BAD_REQUEST, "open_failed", format!("open exited with {st}"))),
        Err(e) => Err(err(StatusCode::INTERNAL_SERVER_ERROR, "open_failed", e.to_string())),
    }
}
