// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Closure durability, Undo, pinned sources and immutable history.
use std::fs;
use serde_json::json;
use crate::checkpoint::Snapshot;
use crate::recovery_store::{Job, Store};

fn fixture(name: &str) -> (std::path::PathBuf, Store, Snapshot) {
    let dir = std::env::temp_dir().join(format!("fbd-closure-{name}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    let pane = |id| json!({"id":id,"profile":null,"cwd":"/Users/alex/project","cwd_status":"known","observed":1,"job":"zsh",
        "grid":{"width":80,"height":24},"connection":{"kind":"shell"}});
    let snapshot = serde_json::from_value(json!({"version":1,"epoch":"run-a","captured":1,"servers":[],"warnings":[],"windows":[{
        "id":"window","frame":{"origin":{"x":0,"y":0},"size":{"width":900,"height":650}},"fullscreen":false,"active":"tab",
        "tabs":[{"id":"tab","control":false,"active":"a","tree":{"vertical":true,"children":[{"pane":"a"},{"pane":"b"}]},"panes":[pane("a"),pane("b")]}]}]})).unwrap();
    (dir.clone(), Store::load(dir), snapshot)
}

#[test]
fn observed_exit_filters_every_retained_source_and_survives_reload() {
    let (dir, mut s, mut snapshot) = fixture("history");
    let a = s.capture(snapshot.clone(), true).unwrap().unwrap();
    snapshot.windows[0].frame.size.width += 10.;
    let b = s.capture(snapshot, true).unwrap().unwrap();
    s.lifecycle("run-a", "a".into(), None, false).unwrap();
    let loaded = Store::load(dir.clone());
    for selected in [&a, &b] {
        assert_eq!(loaded.snapshot(selected).unwrap().count(), 2, "immutable source remains intact");
        let effective = loaded.restorable(selected).unwrap();
        assert_eq!(effective.count(), 1);
        assert_eq!(effective.windows[0].tabs[0].active.as_deref(), Some("b"));
        assert!(matches!(&effective.windows[0].tabs[0].tree, crate::checkpoint::Layout::Leaf(p) if p.pane == "b"));
    }
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn close_before_journal_ack_filters_pinned_source_and_live_undo_revives_it() {
    let (dir, mut s, snapshot) = fixture("pinned");
    let chosen = s.capture(snapshot, true).unwrap().unwrap();
    s.prepare_startup("run-b".into()).unwrap();
    let job = s.begin_startup("run-b").unwrap().unwrap();
    assert!(job.steps.is_empty());
    let marker = format!("iterm-enhancer Restore {chosen}:a");
    s.lifecycle("run-b", "created-before-ack".into(), Some(&marker), false).unwrap();
    assert_eq!(s.restorable(&chosen).unwrap().count(), 1);
    assert!(s.index.startup.as_ref().unwrap().pending());
    assert!(s.capture(s.snapshot(&chosen).unwrap(), true).is_err());
    s.lifecycle("run-b", "created-before-ack".into(), Some(&marker), true).unwrap();
    assert_eq!(s.restorable(&chosen).unwrap().count(), 2);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn journal_alias_closure_and_all_closed_startup_never_resurrect() {
    let (dir, mut s, snapshot) = fixture("alias");
    let chosen = s.capture(snapshot, true).unwrap().unwrap();
    let step = crate::recovery_store::Step { state:"restored".into(), session:Some("created".into()), ..Default::default() };
    s.job(Job { id:chosen.clone(), snapshot:chosen.clone(), epoch:Some("run-a".into()), status:"complete".into(),
        steps:std::collections::HashMap::from([("a".into(),step)]) }).unwrap();
    s.lifecycle("run-a", "created".into(), None, false).unwrap();
    s.lifecycle("run-a", "b".into(), None, false).unwrap();
    assert!(s.restorable(&chosen).unwrap().windows.is_empty());
    assert_eq!(s.prepare_startup("run-b".into()).unwrap().phase, "done");
    assert!(s.begin_startup("run-b").unwrap().is_none());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn stale_events_failed_commits_and_corrupt_history_preserve_state() {
    let (dir, mut s, snapshot) = fixture("failures");
    let chosen = s.capture(snapshot, true).unwrap().unwrap();
    assert!(s.lifecycle("run-old", "a".into(), None, false).is_err());
    assert!(s.index.retired.is_empty());
    let bytes = fs::read(dir.join("index.json")).unwrap();
    fs::remove_file(dir.join("index.json")).unwrap();
    fs::create_dir(dir.join("index.json")).unwrap();
    assert!(s.lifecycle("run-a", "a".into(), None, false).is_err());
    assert!(s.index.retired.is_empty());
    fs::remove_dir(dir.join("index.json")).unwrap();
    fs::write(dir.join("index.json"), bytes).unwrap();
    fs::write(dir.join(format!("{chosen}.json")), "{broken").unwrap();
    assert!(s.lifecycle("run-a", "a".into(), None, false).is_err());
    assert_eq!(fs::read_to_string(dir.join(format!("{chosen}.json"))).unwrap(), "{broken");
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn closure_gc_keeps_all_retained_history_and_rejects_foreign_markers() {
    let (dir, mut s, mut snapshot) = fixture("gc");
    let chosen = s.capture(snapshot.clone(), true).unwrap().unwrap();
    s.lifecycle("run-a", "foreign".into(), Some("iterm-enhancer Restore ffffffffffffffff:a"), false).unwrap();
    assert!(!s.index.retired.contains("a"));
    s.lifecycle("run-a", "a".into(), None, false).unwrap();
    assert!(!s.index.retired.contains("foreign"));
    for i in 0..70 {
        snapshot.windows[0].frame.size.width = 910. + i as f64;
        s.capture(snapshot.clone(), true).unwrap();
        assert!(s.index.retired.contains("a"), "closed GUID still referenced by retained history");
    }
    assert!(s.index.entries.len() <= 64);
    assert!(s.index.entries.iter().any(|e| e.id != chosen));
    s.lifecycle("run-a", "unused".into(), None, false).unwrap();
    assert!(s.capture(snapshot, true).unwrap().is_none(), "unchanged generation still collects unreferenced closures");
    assert!(!s.index.retired.contains("unused"));
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn older_index_without_closure_field_migrates_and_oversized_set_is_blocked() {
    let (dir, mut s, snapshot) = fixture("upgrade");
    s.capture(snapshot, true).unwrap();
    let mut index = serde_json::to_value(&s.index).unwrap();
    index.as_object_mut().unwrap().remove("retired");
    fs::write(dir.join("index.json"), serde_json::to_vec(&index).unwrap()).unwrap();
    assert!(Store::load(dir.clone()).index.retired.is_empty());
    index["retired"] = json!((0..16385).map(|i| format!("sid-{i}")).collect::<Vec<_>>());
    fs::write(dir.join("index.json"), serde_json::to_vec(&index).unwrap()).unwrap();
    assert!(Store::load(dir.clone()).writable().is_err());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn retired_control_pane_never_recreates_physical_tmux_graph_but_plain_client_close_does_not_kill_it() {
    for control in [false, true] {
        let (dir, mut s, mut snapshot) = fixture(if control { "control-graph" } else { "client-graph" });
        snapshot.servers = serde_json::from_value(json!([{"id":"server","local":true,"socket":"/tmp/fixture-tmux.sock","args":[],"pid":1,"started":1,
            "sessions":[{"id":"$0","name":"fixture","created":1,"windows":[{"id":"@0","index":0,"layout":"abcd,80x24,0,0,0","active":"%0",
                "panes":[{"id":"%0","cwd":"/Users/alex/project","job":"zsh","grid":{"width":80,"height":24}},
                         {"id":"%1","cwd":"/Users/alex/project","job":"zsh","grid":{"width":80,"height":24}}]}]}]}])).unwrap();
        let tab = &mut snapshot.windows[0].tabs[0];
        tab.control = control;
        for (i, pane) in tab.panes.iter_mut().enumerate() {
            pane.connection = crate::checkpoint::Connection::Tmux { server:"server".into(), session:"$0".into(), pane:format!("%{i}"), control };
        }
        let chosen = s.capture(snapshot, true).unwrap().unwrap();
        s.lifecycle("run-a", "a".into(), None, false).unwrap();
        let effective = s.restorable(&chosen).unwrap();
        let connection = &effective.windows[0].tabs[0].panes[0].connection;
        assert_eq!(matches!(connection, crate::checkpoint::Connection::Unsupported { .. }), control);
        assert_eq!(effective.servers[0].sessions[0].windows[0].panes.len(), 2, "raw metadata remains intact");
        fs::remove_dir_all(dir).unwrap();
    }
}
