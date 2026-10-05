// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Durable startup boundaries, migration, opt-out and source preservation.
use std::{fs, path::PathBuf};
use serde_json::json;
use crate::{checkpoint::Snapshot, recovery_store::{Job, Store}};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!("fb-startup-{}", crate::auth::random_hex::<8>())))
    }
    fn load(&self) -> Store { Store::load(self.0.clone()) }
}
impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

fn snapshot(epoch: &str) -> Snapshot {
    serde_json::from_value(json!({"version":1,"epoch":epoch,"captured":1,"servers":[],"warnings":[],"windows":[{
        "id":"window","frame":{"origin":{"x":0,"y":0},"size":{"width":900,"height":650}},"fullscreen":false,"active":"tab",
        "tabs":[{"id":"tab","control":false,"active":"pane","tree":{"pane":"pane"},"panes":[{
            "id":"pane","profile":null,"cwd":"/Users/alex/project","cwd_status":"known","observed":1,"job":"zsh",
            "grid":{"width":80,"height":24},"connection":{"kind":"shell"}}]}]}]})).unwrap()
}
fn source(f: &Fixture) -> String { f.load().capture(snapshot("boot-a:iterm-a"), true).unwrap().unwrap() }
fn complete(s: &mut Store, job: Job) {
    s.job(Job { status: "complete".into(), ..job }).unwrap();
}

#[test]
fn fresh_install_is_automatic_but_empty_history_never_creates_a_job() {
    let f = Fixture::new(); let mut s = f.load();
    assert!(s.index.enabled);
    assert!(!s.prepare_startup("boot-a:iterm-a".into()).unwrap().pending());
    assert!(s.begin_startup("boot-a:iterm-a").unwrap().is_none());
    assert!(f.load().job.is_none());
    assert!(s.capture(snapshot("wrong-process"), true).is_err());
    s.capture(snapshot("boot-a:iterm-a"), true).unwrap();
}

#[test]
fn legacy_same_process_upgrade_never_restores_closed_terminals() {
    let f = Fixture::new(); source(&f);
    let index_path = f.0.join("index.json");
    let mut old: serde_json::Value = serde_json::from_slice(&fs::read(&index_path).unwrap()).unwrap();
    old.as_object_mut().unwrap().remove("startup");
    fs::write(index_path, serde_json::to_vec(&old).unwrap()).unwrap();
    let mut s = f.load();
    assert!(s.error.is_none());
    assert!(!s.prepare_startup("boot-a:iterm-a".into()).unwrap().pending());
    assert!(s.index.startup.as_ref().unwrap().snapshot.is_none());
    assert!(s.job.is_none());
    // A new iTerm2 process on the same boot still requires restoration.
    assert!(s.prepare_startup("boot-a:iterm-b".into()).unwrap().pending());
}

#[test]
fn explicit_opt_out_survives_upgrade_and_is_rechecked_before_begin() {
    let f = Fixture::new(); source(&f);
    f.load().enabled(false).unwrap();
    let mut s = f.load();
    assert!(!s.prepare_startup("boot-b:iterm-b".into()).unwrap().pending());
    assert!(!f.load().index.enabled);
    s.enabled(true).unwrap();
    s.prepare_startup("boot-b:iterm-c".into()).unwrap();
    s.enabled(false).unwrap();
    assert!(s.begin_startup("boot-b:iterm-c").unwrap().is_none());
    assert!(!f.load().index.startup.unwrap().pending());
    assert!(s.job.is_none());
}

#[test]
fn intentional_empty_latest_state_does_not_resurrect_stable_history() {
    let f = Fixture::new(); let previous = source(&f); let mut s = f.load();
    let mut empty = snapshot("boot-a:iterm-a"); empty.windows.clear();
    let latest = s.capture(empty, false).unwrap().unwrap();
    let plan = s.prepare_startup("boot-b:iterm-b".into()).unwrap();
    assert_eq!(plan.snapshot, Some(latest));
    assert!(!plan.pending());
    assert!(s.job.is_none());
    assert!(s.snapshot(&previous).is_ok(), "manual history remains available");
}

#[test]
fn reservation_survives_bridge_and_backend_restart_before_and_after_begin() {
    let f = Fixture::new(); let selected = source(&f); let mut s = f.load();
    assert_eq!(s.prepare_startup("boot-b:iterm-b".into()).unwrap().snapshot.as_deref(), Some(selected.as_str()));
    assert!(s.capture(snapshot("boot-b:iterm-b"), true).is_err());
    let mut restarted = f.load();
    assert_eq!(restarted.prepare_startup("boot-b:iterm-b".into()).unwrap().snapshot.as_deref(), Some(selected.as_str()));
    let job = restarted.begin_startup("boot-b:iterm-b").unwrap().unwrap();
    let mut upgrade = f.load();
    assert_eq!(upgrade.begin_startup("boot-b:iterm-b").unwrap().unwrap().id, job.id);
    assert!(upgrade.finish_startup("boot-b:iterm-b").is_err());
    assert!(upgrade.capture(snapshot("boot-b:iterm-b"), false).is_err());
    complete(&mut upgrade, job);
    // A crash after completion and before finish must retain the same completed journal.
    let mut again = f.load();
    assert_eq!(again.job.as_ref().unwrap().status, "complete");
    again.finish_startup("boot-b:iterm-b").unwrap();
    let mut done = f.load();
    assert!(!done.prepare_startup("boot-b:iterm-b".into()).unwrap().pending());
    assert!(done.begin_startup("boot-b:iterm-b").unwrap().is_none());
    assert!(done.capture(snapshot("boot-b:iterm-b"), false).is_ok());
}

#[test]
fn opt_out_mid_run_does_not_interrupt_the_actor() {
    let f = Fixture::new(); source(&f); let mut s = f.load();
    s.prepare_startup("boot-b:iterm-b".into()).unwrap();
    let job = s.begin_startup("boot-b:iterm-b").unwrap().unwrap();
    s.enabled(false).unwrap();
    assert!(s.finish_startup("boot-b:iterm-b").is_err());
    complete(&mut s, job);
    s.finish_startup("boot-b:iterm-b").unwrap();
    assert!(!f.load().index.enabled);
}

#[test]
fn each_durable_write_failure_preserves_the_source_and_retry_identity() {
    let f = Fixture::new(); let selected = source(&f); let mut s = f.load();
    let bytes = fs::read(f.0.join("index.json")).unwrap();
    fs::create_dir(f.0.join("index.json.tmp")).unwrap();
    assert!(s.prepare_startup("boot-b:iterm-b".into()).is_err());
    assert_eq!(fs::read(f.0.join("index.json")).unwrap(), bytes);
    assert_eq!(f.load().index.epoch, "boot-a:iterm-a");
    fs::remove_dir(f.0.join("index.json.tmp")).unwrap();
    s.prepare_startup("boot-b:iterm-b".into()).unwrap();
    fs::create_dir(f.0.join("job.json.tmp")).unwrap();
    assert!(s.begin_startup("boot-b:iterm-b").is_err());
    assert!(f.load().job.is_none());
    assert!(f.load().index.startup.unwrap().pending());
    fs::remove_dir(f.0.join("job.json.tmp")).unwrap();
    let job = s.begin_startup("boot-b:iterm-b").unwrap().unwrap();
    complete(&mut s, job.clone());
    fs::create_dir(f.0.join("index.json.tmp")).unwrap();
    assert!(s.finish_startup("boot-b:iterm-b").is_err());
    assert!(f.load().index.startup.unwrap().pending());
    assert_eq!(f.load().job.unwrap().id, job.id);
    assert!(s.snapshot(&selected).is_ok());
    fs::remove_dir(f.0.join("index.json.tmp")).unwrap();
    s.finish_startup("boot-b:iterm-b").unwrap();
}

#[test]
fn missing_corrupt_and_future_source_never_fall_back_to_older_history() {
    for corruption in [None, Some("{broken"), Some("{\"version\":2}")] {
        let f = Fixture::new(); source(&f); let mut s = f.load();
        let selected = s.capture(snapshot("boot-a:iterm-second"), true).unwrap().unwrap();
        let path = f.0.join(format!("{selected}.json"));
        match corruption { Some(contents) => fs::write(&path, contents).unwrap(), None => fs::remove_file(&path).unwrap() }
        assert!(s.prepare_startup("boot-b:iterm-b".into()).is_err());
        assert_eq!(s.index.epoch, "boot-a:iterm-second");
        assert!(s.job.is_none());
        if let Some(contents) = corruption { assert_eq!(fs::read_to_string(path).unwrap(), contents); }
    }
}

#[test]
fn new_process_retires_an_old_running_job_without_using_it_as_the_source() {
    let f = Fixture::new(); let older = source(&f); let mut s = f.load();
    let latest = s.capture(snapshot("boot-a:iterm-second"), true).unwrap().unwrap();
    s.job(Job { id: older.clone(), snapshot: older, epoch: None, status: "running".into(), steps: Default::default() }).unwrap();
    s.prepare_startup("boot-b:iterm-b".into()).unwrap();
    assert_eq!(s.begin_startup("boot-b:iterm-b").unwrap().unwrap().snapshot, latest);
    assert!(s.epoch("bad-legacy-reset".into()).is_err());
}

#[test]
fn unknown_index_version_fails_closed_without_overwriting_metadata() {
    let f = Fixture::new(); source(&f);
    let path = f.0.join("index.json");
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    value["version"] = json!(2);
    let bytes = serde_json::to_vec(&value).unwrap(); fs::write(&path, &bytes).unwrap();
    let mut s = f.load();
    assert!(s.prepare_startup("boot-b:iterm-b".into()).is_err());
    assert!(s.capture(snapshot("boot-b:iterm-b"), true).is_err());
    assert_eq!(fs::read(path).unwrap(), bytes);
}

#[test]
fn previous_process_complete_job_cannot_acknowledge_the_new_startup() {
    let f = Fixture::new(); source(&f); let mut s = f.load();
    s.prepare_startup("boot-b:iterm-b".into()).unwrap();
    let job = s.begin_startup("boot-b:iterm-b").unwrap().unwrap();
    complete(&mut s, job);
    s.finish_startup("boot-b:iterm-b").unwrap();
    // Quit/crash before the first fresh capture: latest source and job id are identical.
    s.prepare_startup("boot-b:iterm-c".into()).unwrap();
    assert!(s.finish_startup("boot-b:iterm-c").is_err());
    let next = s.begin_startup("boot-b:iterm-c").unwrap().unwrap();
    assert_eq!(next.epoch.as_deref(), Some("boot-b:iterm-c"));
    assert_eq!(next.status, "running");
    complete(&mut s, next);
    s.finish_startup("boot-b:iterm-c").unwrap();
}

#[test]
fn explicit_opt_out_does_not_require_a_readable_source() {
    for missing in [true, false] {
        let f = Fixture::new(); let selected = source(&f); let mut s = f.load();
        s.enabled(false).unwrap();
        let path = f.0.join(format!("{selected}.json"));
        if missing { fs::remove_file(&path).unwrap(); } else { fs::write(&path, "{broken").unwrap(); }
        let mut restarted = f.load();
        assert!(!restarted.prepare_startup("boot-b:iterm-b".into()).unwrap().pending());
        assert!(restarted.job.is_none());
        assert!(restarted.capture(snapshot("boot-b:iterm-b"), true).is_ok());
        if !missing { assert_eq!(fs::read_to_string(path).unwrap(), "{broken"); }
    }
}

#[tokio::test]
async fn delayed_progress_from_the_previous_process_cannot_acknowledge_new_startup() {
    let f = Fixture::new();
    let app = crate::tests::app(f.0.join("workspaces.json"));
    let mut old;
    let mut current;
    {
        let mut s = app.recovery.lock();
        s.capture(snapshot("boot-a:iterm-a"), true).unwrap();
        s.prepare_startup("boot-b:iterm-b".into()).unwrap();
        old = s.begin_startup("boot-b:iterm-b").unwrap().unwrap();
        s.prepare_startup("boot-b:iterm-c".into()).unwrap();
        current = s.begin_startup("boot-b:iterm-c").unwrap().unwrap();
    }
    old.status = "complete".into();
    assert!(crate::recovery::progress(axum::extract::State(app.clone()), axum::Json(old)).await.is_err());
    assert_eq!(app.recovery.lock().job.as_ref().unwrap().status, "running");
    current.status = "complete".into();
    assert!(crate::recovery::progress(axum::extract::State(app.clone()), axum::Json(current)).await.is_ok());
    app.recovery.lock().finish_startup("boot-b:iterm-c").unwrap();
}
