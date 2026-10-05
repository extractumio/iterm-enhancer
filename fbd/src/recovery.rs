// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Authenticated recovery controls; durable I/O runs off async workers.
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::checkpoint::Snapshot;
use crate::events::Event;
use crate::http::{err, ApiResult};
use crate::recovery_store::{Job, Store};
use crate::Shared;

pub fn status(store: &Store) -> Value {
    json!({"enabled":store.index.enabled,"epoch":store.index.epoch,"entries":store.index.entries,
        "recommended":store.recommended(),"startup":store.index.startup,"retired":store.index.retired,"job":store.job,"error":store.error,"instance":store.instance,"revision":store.revision})
}

pub(crate) async fn announce(app: &Shared) {
    if let Ok(value) = disk(app, |s| Ok(status(s))).await { app.bus.send(Event::Recovery(value)); }
}

pub async fn current(app: &Shared) -> Option<Value> { disk(app, |s| Ok(status(s))).await.ok() }

pub(crate) async fn disk<T: Send + 'static>(app: &Shared, f: impl FnOnce(&mut Store) -> Result<T, String> + Send + 'static) -> Result<T, axum::response::Response> {
    let store = app.recovery.clone();
    tokio::task::spawn_blocking(move || f(&mut store.lock())).await
        .map_err(|_| err(StatusCode::INTERNAL_SERVER_ERROR, "recovery_failed", "Recovery worker failed"))?
        .map_err(|m| err(StatusCode::CONFLICT, "recovery_failed", m))
}

pub async fn get(State(app): State<Shared>) -> ApiResult {
    Ok(Json(disk(&app, |s| Ok(status(s))).await?).into_response())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Enabled { enabled: bool }
pub async fn configure(State(app): State<Shared>, Json(b): Json<Enabled>) -> ApiResult {
    disk(&app, move |s| s.enabled(b.enabled)).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

pub async fn save(State(app): State<Shared>, headers: HeaderMap) -> ApiResult {
    if !app.term.lock().bridge_alive() || app.commands.receiver_count() == 0 { return Err(crate::api_state::no_bridge()); }
    let _ = app.commands.send(json!({"action":"save-checkpoint","by":crate::api_state::client(&headers)}));
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Restore { pub(crate) snapshot: String }
pub async fn restore(State(app): State<Shared>, headers: HeaderMap, Json(b): Json<Restore>) -> ApiResult {
    if !app.term.lock().bridge_alive() || app.commands.receiver_count() == 0 { return Err(crate::api_state::no_bridge()); }
    let (job, launch) = disk(&app, move |s| {
        s.writable()?;
        if s.index.startup.as_ref().is_some_and(|plan| plan.pending() && plan.snapshot.as_ref() != Some(&b.snapshot)) {
            return Err("Automatic recovery pending; retry its reserved checkpoint first".into());
        }
        s.snapshot(&b.snapshot)?;
        if let Some(j) = &s.job {
            if j.status == "running" {
                if j.snapshot == b.snapshot { return Ok((j.clone(), false)); }
                return Err("Another terminal recovery is running".into());
            }
        }
        let mut job = s.job.clone().filter(|j| j.snapshot == b.snapshot).unwrap_or_else(|| Job {
            id: b.snapshot.clone(), snapshot: b.snapshot, epoch: None, status: "running".into(),
            steps: Default::default(),
        });
        job.status = "running".into();
        job.epoch = Some(s.index.epoch.clone());
        s.job(job.clone())?;
        Ok((job, true))
    }).await?;
    if launch {
        let _ = app.commands.send(json!({"action":"restore-checkpoint","job":job.id,"by":crate::api_state::client(&headers)}));
    }
    announce(&app).await;
    Ok(Json(job).into_response())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Capture { snapshot: Snapshot, force: bool }
pub async fn capture(State(app): State<Shared>, Json(b): Json<Capture>) -> ApiResult {
    let result = disk(&app, move |s| {
        if !b.force && !s.index.enabled { return Ok(None); }
        s.capture(b.snapshot, b.force)
    }).await;
    match result {
        Ok(id) => {
            announce(&app).await;
            Ok(Json(json!({"id":id})).into_response())
        }
        Err(e) => Err(e),
    }
}

pub async fn snapshot(State(app): State<Shared>, Path(id): Path<String>) -> ApiResult {
    let value = disk(&app, move |s| s.restorable(&id)).await?;
    Ok(Json(value).into_response())
}

pub async fn progress(State(app): State<Shared>, Json(job): Json<Job>) -> ApiResult {
    disk(&app, move |s| {
        if !s.job.as_ref().is_some_and(|j| j.id == job.id && j.snapshot == job.snapshot && j.epoch == job.epoch) {
            return Err("Recovery job changed".into());
        }
        if !matches!(job.status.as_str(), "running" | "complete" | "interrupted") { return Err("Invalid recovery status".into()); }
        s.job(job)
    }).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Error { message: String }
pub async fn error(State(app): State<Shared>, Json(b): Json<Error>) -> ApiResult {
    let message: String = b.message.chars().filter(|c| !c.is_control()).take(512).collect();
    disk(&app, move |s| { if s.error.as_ref() != Some(&message) { s.error = Some(message); s.revision += 1; } Ok(()) }).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Epoch { epoch: String }
pub async fn epoch(State(app): State<Shared>, Json(b): Json<Epoch>) -> ApiResult {
    disk(&app, move |s| s.epoch(b.epoch)).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Lifecycle { epoch: String, session: String, marker: Option<String>, revive: bool }
pub async fn lifecycle(State(app): State<Shared>, Json(b): Json<Lifecycle>) -> ApiResult {
    disk(&app, move |s| s.lifecycle(&b.epoch, b.session, b.marker.as_deref(), b.revive)).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

pub async fn startup(State(app): State<Shared>, Json(b): Json<Epoch>) -> ApiResult {
    let plan = disk(&app, move |s| s.prepare_startup(b.epoch)).await?;
    announce(&app).await;
    Ok(Json(plan).into_response())
}

pub async fn normal_exit(State(app): State<Shared>, Json(b): Json<Epoch>) -> ApiResult {
    disk(&app, move |s| s.normal_exit(&b.epoch)).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

pub async fn startup_begin(State(app): State<Shared>, Json(b): Json<Epoch>) -> ApiResult {
    let job = disk(&app, move |s| s.begin_startup(&b.epoch)).await?;
    announce(&app).await;
    Ok(Json(job).into_response())
}

pub async fn startup_finish(State(app): State<Shared>, Json(b): Json<Epoch>) -> ApiResult {
    disk(&app, move |s| s.finish_startup(&b.epoch)).await?;
    announce(&app).await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[cfg(test)]
mod tests {
    #[test]
    fn checkpoint_ids_never_become_arbitrary_paths() {
        assert!(crate::recovery_store::id("0123456789abcdef"));
        for bad in ["../index", "../../../a", "000000000000000g", ""] { assert!(!crate::recovery_store::id(bad)); }
    }
}
