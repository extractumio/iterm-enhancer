// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Same-boot normal relaunch, reboot override and failure-safe exit metadata.
use std::{fs, path::PathBuf};
use serde_json::json;
use crate::{checkpoint::Snapshot, recovery_store::{Job, Store}};

const FIRST: &str = "boot-a:42:100";
const NEXT: &str = "boot-a:43:200";

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self { Self(std::env::temp_dir().join(format!("fb-quit-{}", crate::auth::random_hex::<8>()))) }
    fn load(&self) -> Store { Store::load(self.0.clone()) }
    fn source(&self) -> String { self.load().capture(snapshot(FIRST), true).unwrap().unwrap() }
}
impl Drop for Fixture { fn drop(&mut self) { let _ = fs::remove_dir_all(&self.0); } }

fn snapshot(epoch: &str) -> Snapshot {
    serde_json::from_value(json!({"version":1,"epoch":epoch,"captured":1,"servers":[],"warnings":[],"windows":[{
        "id":"window","frame":{"origin":{"x":0,"y":0},"size":{"width":900,"height":650}},"fullscreen":false,"active":"tab",
        "tabs":[{"id":"tab","control":false,"active":"pane","tree":{"pane":"pane"},"panes":[{
            "id":"pane","profile":null,"cwd":"/Users/alex/project","cwd_status":"known","observed":1,"job":"zsh",
            "grid":{"width":80,"height":24},"connection":{"kind":"shell"}}]}]}]})).unwrap()
}

#[test]
fn normal_same_boot_relaunch_skips_once_keeps_manual_source_and_capture_enabled() {
    let f = Fixture::new(); let source = f.source(); let mut s = f.load();
    s.normal_exit(FIRST).unwrap();
    let revision = s.revision;
    s.normal_exit(FIRST).unwrap();
    assert_eq!(s.revision, revision, "duplicate acknowledgement writes nothing");
    let mut s = f.load(); let plan = s.prepare_startup(NEXT.into()).unwrap();
    assert!(plan.skipped && !plan.pending() && s.index.enabled);
    assert_eq!(plan.snapshot.as_deref(), Some(source.as_str()));
    assert!(s.index.clean_exit.is_none());
    assert!(s.begin_startup(NEXT).unwrap().is_none());
    assert!(s.restorable(&source).is_ok());
    assert!(f.load().prepare_startup(NEXT.into()).unwrap().skipped);
    s.capture(snapshot(NEXT), true).unwrap();
    // The skipped launch does not suppress a later unknown/crashed process.
    assert!(s.prepare_startup("boot-a:44:300".into()).unwrap().pending());
}

#[test]
fn new_boot_overrides_normal_exit_and_unknown_same_boot_exit_still_restores() {
    for next in [NEXT, "boot-b:43:200"] {
        let f = Fixture::new(); f.source(); let mut s = f.load();
        if next.starts_with("boot-b") { s.normal_exit(FIRST).unwrap(); }
        let plan = s.prepare_startup(next.into()).unwrap();
        assert!(plan.pending() && !plan.skipped);
        assert!(s.index.clean_exit.is_none());
    }
}

#[test]
fn stale_exit_and_invalid_loaded_markers_cannot_suppress_another_process() {
    let f = Fixture::new(); f.source(); let mut s = f.load();
    s.normal_exit(FIRST).unwrap(); s.prepare_startup(NEXT.into()).unwrap();
    assert_eq!(s.normal_exit(FIRST).unwrap_err(), "Application exit belongs to a different iTerm2 process");
    assert!(s.index.clean_exit.is_none());
    let path = f.0.join("index.json");
    let bytes = fs::read(&path).unwrap();
    for marker in [json!(FIRST), json!(""), json!("bad\nidentity"), json!(true)] {
        let mut index: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        index["clean_exit"] = marker;
        fs::write(&path, serde_json::to_vec(&index).unwrap()).unwrap();
        let mut loaded = f.load(); assert!(loaded.error.is_some());
        assert!(loaded.normal_exit(NEXT).is_err());
    }
}

#[test]
fn failed_exit_write_never_suppresses_crash_recovery_or_overwrites_history() {
    let f = Fixture::new(); let source = f.source(); let mut s = f.load();
    let path = f.0.join("index.json"); let before = fs::read(&path).unwrap();
    fs::remove_file(&path).unwrap(); fs::create_dir(&path).unwrap();
    assert!(s.normal_exit(FIRST).is_err());
    assert!(s.index.clean_exit.is_none());
    assert!(s.snapshot(&source).is_ok());
    fs::remove_dir(&path).unwrap(); fs::write(path, before).unwrap();
    assert!(f.load().prepare_startup(NEXT.into()).unwrap().pending());
}

#[test]
fn normal_skip_preserves_a_corrupt_source_without_requiring_automatic_reconstruction() {
    let f = Fixture::new(); let source = f.source(); let mut s = f.load(); s.normal_exit(FIRST).unwrap();
    let path = f.0.join(format!("{source}.json")); fs::write(&path, b"broken checkpoint").unwrap();
    let mut s = f.load(); let plan = s.prepare_startup(NEXT.into()).unwrap();
    assert!(plan.skipped && !plan.pending());
    assert_eq!(plan.snapshot.as_deref(), Some(source.as_str()));
    assert!(s.restorable(&source).unwrap_err().contains("Checkpoint unreadable"));
    assert_eq!(fs::read(path).unwrap(), b"broken checkpoint");
}

#[test]
fn skipped_launch_interrupts_old_actor_before_index_commit_and_recovers_commit_failure() {
    let f = Fixture::new(); let source = f.source(); let mut s = f.load();
    s.job(Job { id:source.clone(), snapshot:source.clone(), epoch:Some(FIRST.into()), status:"running".into(), steps:Default::default() }).unwrap();
    s.normal_exit(FIRST).unwrap();
    let path = f.0.join("index.json"); let before = fs::read(&path).unwrap();
    fs::remove_file(&path).unwrap(); fs::create_dir(&path).unwrap();
    assert!(s.prepare_startup(NEXT.into()).is_err());
    assert_eq!(s.index.epoch, FIRST);
    assert_eq!(s.job.as_ref().unwrap().status, "interrupted");
    fs::remove_dir(&path).unwrap(); fs::write(path, before).unwrap();
    let mut s = f.load(); assert!(s.prepare_startup(NEXT.into()).unwrap().skipped);
    assert_eq!(s.job.as_ref().unwrap().status, "interrupted");
    s.capture(snapshot(NEXT), true).unwrap();
    // A manual actor belonging to the new process must survive same-process restart.
    s.job(Job { id:source.clone(), snapshot:source, epoch:Some(NEXT.into()), status:"running".into(), steps:Default::default() }).unwrap();
    let mut restarted = f.load(); assert!(restarted.prepare_startup(NEXT.into()).unwrap().skipped);
    assert_eq!(restarted.job.as_ref().unwrap().status, "running");
}

#[test]
fn journal_interruption_failure_leaves_exit_and_startup_decision_unconsumed() {
    let f = Fixture::new(); let source = f.source(); let mut s = f.load();
    s.job(Job { id:source.clone(), snapshot:source, epoch:Some(FIRST.into()), status:"running".into(), steps:Default::default() }).unwrap();
    s.normal_exit(FIRST).unwrap();
    let path = f.0.join("job.json"); fs::remove_file(&path).unwrap(); fs::create_dir(&path).unwrap();
    assert!(s.prepare_startup(NEXT.into()).is_err());
    assert_eq!(s.index.epoch, FIRST);
    assert_eq!(s.index.clean_exit.as_deref(), Some(FIRST));
    assert_eq!(s.job.as_ref().unwrap().status, "running");
    fs::remove_dir(path).unwrap();
    assert!(s.prepare_startup(NEXT.into()).unwrap().skipped);
}

#[test]
fn empty_and_legacy_history_keep_defaults_without_losing_exit_metadata_compatibility() {
    let f = Fixture::new(); let mut s = f.load(); s.prepare_startup(FIRST.into()).unwrap();
    s.normal_exit(FIRST).unwrap(); let plan = s.prepare_startup(NEXT.into()).unwrap();
    assert!(plan.skipped && plan.snapshot.is_none() && s.index.enabled);
    let f = Fixture::new(); f.source(); let path = f.0.join("index.json");
    let mut old: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    old.as_object_mut().unwrap().remove("clean_exit");
    fs::write(&path, serde_json::to_vec(&old).unwrap()).unwrap();
    let mut s = f.load(); assert!(s.error.is_none());
    assert!(s.prepare_startup(NEXT.into()).unwrap().pending());
}

#[test]
fn legacy_epoch_and_capture_transitions_clear_previous_normal_exit_before_reload() {
    for by_capture in [false, true] {
        let f = Fixture::new(); f.source(); let mut s = f.load();
        assert!(s.index.startup.is_none());
        s.normal_exit(FIRST).unwrap();
        if by_capture { s.capture(snapshot(NEXT), true).unwrap(); }
        else { s.epoch(NEXT.into()).unwrap(); }
        let s = f.load(); assert!(s.error.is_none());
        assert_eq!(s.index.epoch, NEXT);
        assert!(s.index.clean_exit.is_none());
    }
}
