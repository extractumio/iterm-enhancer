// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Checkpoints challenge corruption, retention, references and recovery concurrency.
use std::fs;
use std::path::PathBuf;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::Json;
use serde_json::json;
use crate::checkpoint::Snapshot;
use crate::recovery_store::{Job, Store};

fn folder(name: &str) -> PathBuf { std::env::temp_dir().join(format!("fbd-recovery-{name}-{}", std::process::id())) }
fn snapshot(epoch: &str, captured: u64) -> Snapshot {
    serde_json::from_value(json!({"version":1,"epoch":epoch,"captured":captured,"servers":[],"warnings":[],"windows":[{
        "id":"window","frame":{"origin":{"x":0,"y":0},"size":{"width":900,"height":650}},"fullscreen":false,"active":"tab",
        "tabs":[{"id":"tab","control":false,"active":"pane","tree":{"pane":"pane"},"panes":[{
            "id":"pane","profile":null,"cwd":"/Users/alex/project","cwd_status":"known","observed":captured,"job":"zsh",
            "grid":{"width":80,"height":24},"connection":{"kind":"shell"}}]}]}]})).unwrap()
}

#[test]
fn transient_errors_do_not_block_capture_and_corrupt_files_are_never_overwritten() {
    let dir = folder("corrupt"); let _ = fs::remove_dir_all(&dir);
    let mut s = Store::load(dir.clone());
    s.capture(snapshot("run-a", 1), true).unwrap();
    s.error = Some("temporary metadata timeout".into());
    s.capture(snapshot("run-b", 2), true).unwrap();
    assert!(s.error.is_none());
    fs::write(dir.join("job.json"), "{broken").unwrap();
    let mut s = Store::load(dir.clone());
    assert!(s.error.as_ref().unwrap().contains("journal"));
    assert!(s.capture(snapshot("run-c", 3), true).is_err());
    assert_eq!(fs::read_to_string(dir.join("job.json")).unwrap(), "{broken");
    fs::remove_file(dir.join("job.json")).unwrap();
    fs::write(dir.join("index.json"), "{broken").unwrap();
    let mut s = Store::load(dir.clone());
    assert!(s.enabled(true).is_err());
    assert_eq!(fs::read_to_string(dir.join("index.json")).unwrap(), "{broken");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn orphan_quota_recovers_only_after_recommitting_the_valid_index() {
    let dir = folder("quota"); let _ = fs::remove_dir_all(&dir);
    let mut s = Store::load(dir.clone());
    let chosen = s.capture(snapshot("run-a", 1), true).unwrap().unwrap();
    s.job(Job { id: chosen.clone(), snapshot: chosen.clone(), epoch: None, status: "interrupted".into(), steps: Default::default() }).unwrap();
    let orphan = dir.join("ffffffffffffffff.json");
    fs::File::create(&orphan).unwrap().set_len(132 << 20).unwrap();
    let saved = fs::read(dir.join("index.json")).unwrap();
    fs::remove_file(dir.join("index.json")).unwrap();
    fs::create_dir(dir.join("index.json")).unwrap();
    assert!(s.capture(snapshot("run-b", 2), true).is_err());
    assert!(orphan.exists(), "uncommitted index never permits orphan deletion");
    assert!(s.snapshot(&chosen).is_ok());
    fs::remove_dir(dir.join("index.json")).unwrap();
    fs::write(dir.join("index.json"), saved).unwrap();
    assert!(s.capture(snapshot("run-b", 2), true).unwrap().is_some());
    assert!(!orphan.exists());
    assert!(s.snapshot(&chosen).is_ok(), "recovery source remains pinned");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn current_epoch_empty_capture_history_and_job_source_remain_protected() {
    let dir = folder("retain"); let _ = fs::remove_dir_all(&dir);
    let mut s = Store::load(dir.clone());
    let chosen = s.capture(snapshot("run-a", 1), true).unwrap().unwrap();
    let latest = s.capture(snapshot("run-b", 2), true).unwrap().unwrap();
    s.epoch("run-c".into()).unwrap();
    assert_eq!(s.recommended(), Some(latest));
    s.job(Job { id: chosen.clone(), snapshot: chosen.clone(), epoch: None, status: "interrupted".into(), steps: Default::default() }).unwrap();
    for i in 0..80 {
        let mut p = snapshot("run-c", i);
        p.windows[0].frame.size.width += i as f64;
        s.capture(p, true).unwrap();
    }
    assert!(s.index.entries.len() <= 64);
    assert!(s.snapshot(&chosen).is_ok());
    let mut empty = snapshot("run-d", 100); empty.windows.clear();
    s.capture(empty, true).unwrap();
    assert!(s.recommended().is_some());
    let files = fs::read_dir(&dir).unwrap().flatten().filter(|e| e.file_name().to_str().is_some_and(|n| n.len() == 21)).count();
    assert_eq!(files, s.index.entries.len(), "orphan immutable files do not accumulate");
    let _ = fs::remove_dir_all(dir);
}

#[test]
fn schema_rejects_dangling_or_executable_targets() {
    let mut s = snapshot("run", 1);
    s.windows[0].tabs[0].panes[0].connection = crate::checkpoint::Connection::Tmux {
        server:"absent".into(), session:"$0".into(), pane:"%0".into(), control:false };
    assert!(s.validate().is_err());
    s.windows[0].tabs[0].panes[0].connection = crate::checkpoint::Connection::Shell;
    s.windows[0].tabs[0].active = Some("absent".into());
    assert!(s.validate().is_err());
    assert!(!crate::checkpoint::ssh_args(&["-p".into(), "app".into(), "devbox.example".into()]));
}

#[tokio::test]
async fn concurrent_requests_join_and_retries_keep_the_same_creation_identity() {
    let dir = folder("concurrent"); let _ = fs::remove_dir_all(dir.with_extension("recovery"));
    let app = crate::tests::app(dir.clone());
    let selected = app.recovery.lock().capture(snapshot("run", 1), true).unwrap().unwrap();
    crate::api_state::internal_state(State(app.clone()), Json(json!({"key":"pane"}))).await;
    let mut commands = app.commands.subscribe();
    let request = || crate::recovery::Restore { snapshot: selected.clone() };
    let (a, b) = tokio::join!(crate::recovery::restore(State(app.clone()), HeaderMap::new(), Json(request())),
                            crate::recovery::restore(State(app.clone()), HeaderMap::new(), Json(request())));
    assert!(a.is_ok() && b.is_ok());
    let first = commands.try_recv().unwrap();
    assert_eq!(first["job"], selected);
    assert!(commands.try_recv().is_err(), "one actor launches for concurrent requests");
    let mut job = app.recovery.lock().job.clone().unwrap(); job.status = "interrupted".into();
    app.recovery.lock().job(job).unwrap();
    assert!(crate::recovery::restore(State(app.clone()), HeaderMap::new(), Json(request())).await.is_ok());
    assert_eq!(commands.try_recv().unwrap()["job"], selected);
    let _ = fs::remove_dir_all(dir.with_extension("recovery"));
    let _ = fs::remove_file(dir);
}
