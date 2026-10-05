// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Per-pane workspace: tree state + open tabs, keyed by the bridge's state key
//! (iTerm2 session id, or tmux pane). Persisted to JSON, debounced, atomic rename.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::Notify;

use crate::events::{Bus, Event};

#[derive(Clone, Serialize, Deserialize, Debug, Default, PartialEq)]
pub struct Tab {
    pub path: String,
    #[serde(default = "default_view")]
    pub view: String,
}

fn default_view() -> String {
    "auto".into()
}

#[derive(Clone, Serialize, Deserialize, Debug, Default)]
pub struct Pane {
    #[serde(default)]
    pub rev: u64,
    #[serde(default)]
    pub root: String,
    #[serde(default)]
    pub expanded: Vec<String>,
    #[serde(default)]
    pub selected: Vec<String>,
    #[serde(default)]
    pub scroll: f64,
    #[serde(default)]
    pub tabs: Vec<Tab>,
    #[serde(default)]
    pub active_tab: Option<usize>,
    #[serde(default)]
    pub updated: u64,
}

#[derive(Serialize, Deserialize, Default)]
struct File {
    version: u32,
    #[serde(default)]
    prefs: Value,
    #[serde(default)]
    panes: HashMap<String, Pane>,
}

pub struct Store {
    data: Mutex<File>,
    writing: Mutex<()>,
    path: PathBuf,
    dirty: Notify,
    bus: Bus,
    ttl: u64,
}

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

impl Store {
    pub fn load(path: PathBuf, ttl_days: u64, bus: Bus) -> Arc<Self> {
        let mut data = match fs::read(&path) {
            Ok(bytes) => match serde_json::from_slice::<File>(&bytes) {
                Ok(f) if f.version == 1 => f,
                _ => {
                    let bak = path.with_extension("json.bak");
                    let _ = fs::rename(&path, &bak);
                    tracing::warn!(event = "workspace.reset", reason = "parse_error", backup = %bak.display());
                    File::default()
                }
            },
            Err(_) => File::default(),
        };
        data.version = 1;
        // Wait for authoritative liveness before pruning: a live inactive pane may
        // predate the TTL when fbd restarts.
        let store = Arc::new(Store { data: Mutex::new(data), writing: Mutex::new(()), path, dirty: Notify::new(), bus,
            ttl: ttl_days.saturating_mul(86400) });
        let s = store.clone();
        tokio::spawn(async move {
            loop {
                s.dirty.notified().await;
                tokio::time::sleep(Duration::from_millis(500)).await;
                let s = s.clone();
                let _ = tokio::task::spawn_blocking(move || s.flush()).await;
            }
        });
        store
    }

    pub fn flush(&self) {
        // Shutdown and the debounce worker must serialize snapshot capture and publication.
        let _writing = self.writing.lock();
        let bytes = serde_json::to_vec(&*self.data.lock()).unwrap_or_default(); // lock held only to serialize
        let tmp = self.path.with_extension("json.tmp");
        if fs::write(&tmp, bytes).and_then(|_| fs::rename(&tmp, &self.path)).is_err() {
            tracing::warn!(event = "workspace.write_failed", path = %self.path.display());
        }
    }

    fn touched(&self, key: &str, rev: u64, by: Option<String>) {
        self.dirty.notify_one();
        self.bus.send(Event::Workspace { key: key.to_string(), rev, by });
    }

    pub fn get(&self, key: &str, cwd: Option<&str>) -> Pane {
        let data = self.data.lock();
        match data.panes.get(key) {
            Some(p) => p.clone(),
            None => Pane { root: cwd.unwrap_or_default().to_string(), ..Default::default() },
        }
    }

    /// The bridge reports the pane's cwd. A new root clears the tree state but keeps tabs.
    pub fn set_root(&self, key: &str, root: &str) {
        let rev = {
            let mut data = self.data.lock();
            let p = data.panes.entry(key.to_string()).or_default();
            if p.root == root {
                return;
            }
            p.root = root.to_string();
            p.expanded.clear();
            p.selected.clear();
            p.scroll = 0.0;
            p.rev += 1;
            p.updated = now();
            p.rev
        };
        self.touched(key, rev, None);
    }

    /// Replace a pane's state if the client saw the latest revision; else the current one.
    pub fn put(&self, key: &str, mut pane: Pane, by: Option<String>) -> Result<u64, Pane> {
        let rev = {
            let mut data = self.data.lock();
            let cur = data.panes.entry(key.to_string()).or_default();
            if pane.rev != cur.rev || (!cur.root.is_empty() && pane.root != cur.root) {
                return Err(cur.clone());
            }
            pane.rev = cur.rev + 1;
            pane.updated = now();
            *cur = pane;
            cur.rev
        };
        self.touched(key, rev, by);
        Ok(rev)
    }

    pub fn prefs(&self) -> Value {
        self.data.lock().prefs.clone()
    }

    pub fn set_prefs(&self, prefs: Value) {
        self.data.lock().prefs = prefs;
        self.dirty.notify_one();
    }

    pub fn len(&self) -> usize {
        self.data.lock().panes.len()
    }

    /// Coarse liveness persistence does not change content revisions or emit updates.
    pub fn maintain(&self, keys: &HashSet<String>, protect_tmux: bool) {
        self.maintain_at(keys, protect_tmux, now());
    }

    fn maintain_at(&self, keys: &HashSet<String>, protect_tmux: bool, at: u64) {
        let mut data = self.data.lock();
        let before = data.panes.len();
        let mut touched = false;
        let cutoff = at.saturating_sub(self.ttl);
        data.panes.retain(|key, pane| {
            if keys.contains(key) {
                if at.saturating_sub(pane.updated) >= 3600 {
                    pane.updated = at;
                    touched = true;
                }
                true
            } else {
                pane.updated >= cutoff || (protect_tmux && key.starts_with("tmux:"))
            }
        });
        if touched || before != data.panes.len() {
            self.dirty.notify_one();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_waiting_flush_captures_the_latest_snapshot_after_the_writer_lock() {
        let path = std::env::temp_dir().join(format!("fbd-ws-flush-{}.json", std::process::id()));
        let s = Arc::new(Store { data: Mutex::new(File { version: 1, ..Default::default() }),
            writing: Mutex::new(()), path: path.clone(), dirty: Notify::new(), bus: Bus::new(), ttl: 14 * 86400 });
        let write = s.writing.lock();
        let (started, waiting) = std::sync::mpsc::channel();
        let thread = {
            let s = s.clone();
            std::thread::spawn(move || { started.send(()).unwrap(); s.flush(); })
        };
        waiting.recv().unwrap();
        s.data.lock().prefs = serde_json::json!({"newest": "large snapshot".repeat(1000)});
        drop(write);
        thread.join().unwrap();
        let saved: File = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(saved.prefs, s.prefs());
        assert!(!path.with_extension("json.tmp").exists());
        fs::remove_file(path).unwrap();
    }

    #[tokio::test]
    async fn ongoing_ttl_preserves_live_idle_panes_and_uncertain_tmux_without_revisions() {
        let path = std::env::temp_dir().join(format!("fbd-ws-ttl-{}.json", std::process::id()));
        fs::write(&path, r#"{"version":1,"panes":{
            "live":{"root":"/a","updated":1,"rev":7},
            "closed":{"root":"/b","updated":1},
            "tmux:devbox:/tmp/t:%1":{"root":"/c","updated":1}}}"#).unwrap();
        let s = Store::load(path.clone(), 14, Bus::new());
        assert_eq!(s.len(), 3, "startup waits for live inventory");
        let at = 15 * 86400;
        let keys = HashSet::from(["live".into()]);
        s.maintain_at(&keys, true, at);
        assert_eq!(s.len(), 2, "closed state purged without restarting");
        let live = s.get("live", None);
        assert_eq!((live.updated, live.rev), (at, 7));
        s.maintain_at(&keys, true, at + 5);
        assert_eq!(s.get("live", None).updated, at, "no five-second disk writes");
        s.maintain_at(&keys, false, at + 10);
        assert_eq!(s.len(), 1, "resolved absence permits tmux TTL cleanup");
        s.flush();
        let loaded = Store::load(path.clone(), 14, Bus::new());
        assert_eq!(loaded.len(), 1);
        let _ = fs::remove_file(path);
    }

    /// AC-35: a build reads state written by a newer one (unknown fields) or an older one
    /// (fields missing), so a rollback or upgrade never loses the workspaces.
    #[tokio::test]
    async fn state_of_other_builds_is_readable() {
        let path = std::env::temp_dir().join(format!("fbd-ws-compat-{}.json", std::process::id()));
        fs::write(&path, r#"{"version": 1, "from_a_newer_build": true, "prefs": {"hidden": false},
            "panes": {"p1": {"root": "/a", "updated": 4102444800, "tabs": [{"path": "/a/x.rs", "pinned": true}], "zoom": 2}},
            "older": {}}"#).unwrap();
        let s = Store::load(path.clone(), 14, Bus::new());
        let p = s.get("p1", None);
        assert_eq!((p.root.as_str(), p.tabs[0].path.as_str(), p.tabs[0].view.as_str()), ("/a", "/a/x.rs", "auto"));
        assert!(p.expanded.is_empty() && p.active_tab.is_none(), "missing fields take their defaults");
        assert_eq!(s.prefs()["hidden"], false);
        assert!(!path.with_extension("json.bak").exists(), "not treated as corrupt");
        let _ = fs::remove_file(&path);
    }

    #[tokio::test]
    async fn cd_clears_tree_keeps_tabs_and_rev_guards() {
        let path = std::env::temp_dir().join(format!("fbd-ws-{}.json", std::process::id()));
        let s = Store::load(path.clone(), 14, Bus::new());
        s.set_root("p1", "/a");
        let mut p = s.get("p1", None);
        assert_eq!((p.root.as_str(), p.rev), ("/a", 1));
        p.expanded = vec!["/a/src".into()];
        p.selected = vec!["/a/src/x.rs".into()];
        p.tabs = vec![Tab { path: "/a/README.md".into(), view: "rendered".into() }];
        let rev = s.put("p1", p.clone(), None).ok().unwrap();
        assert_eq!(rev, 2);
        // a second panel still holding rev 1 is refused
        assert!(matches!(s.put("p1", p.clone(), None), Err(cur) if cur.rev == 2));
        s.set_root("p1", "/b");
        let p = s.get("p1", None);
        assert!(p.expanded.is_empty() && p.selected.is_empty());
        assert_eq!(p.tabs.len(), 1);
        assert_eq!(p.root, "/b");
        s.flush();
        let s2 = Store::load(path.clone(), 14, Bus::new());
        assert_eq!(s2.get("p1", None).tabs.len(), 1);
        fs::write(&path, "{not json").unwrap();
        let s3 = Store::load(path.clone(), 14, Bus::new());
        assert_eq!(s3.len(), 0);
        assert!(path.with_extension("json.bak").exists());
        let _ = fs::remove_file(path.with_extension("json.bak"));
    }
}
