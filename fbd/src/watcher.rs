// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Live refresh (AC-13): FSEvents on the folders the focused pane shows (root, expanded
//! folders, folders of open tabs). Changes are batched for 500 ms, so a burst like
//! `npm install` costs at most two refreshes per second.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use notify::{EventKind, RecursiveMode, Watcher as _};
use parking_lot::Mutex;
use tokio::sync::mpsc;

use crate::events::{publish_change, Bus};
use crate::listing::Cache;

const BATCH: Duration = Duration::from_millis(500);

pub struct Watcher {
    inner: Mutex<(notify::RecommendedWatcher, HashSet<PathBuf>)>,
}

impl Watcher {
    pub fn start(cache: Arc<Cache>, bus: Bus) -> notify::Result<Arc<Self>> {
        let (tx, mut rx) = mpsc::unbounded_channel::<PathBuf>();
        let w = notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
            if let Ok(ev) = res {
                if matches!(ev.kind, EventKind::Access(_)) {
                    return;
                }
                for p in ev.paths {
                    let _ = tx.send(p);
                }
            }
        })?;
        let me = Arc::new(Watcher { inner: Mutex::new((w, HashSet::new())) });
        let watched = me.clone();
        tokio::spawn(async move {
            while let Some(first) = rx.recv().await {
                let mut batch: HashSet<PathBuf> = HashSet::from([first]);
                let deadline = tokio::time::Instant::now() + BATCH;
                while let Ok(Some(p)) = tokio::time::timeout_at(deadline, rx.recv()).await {
                    batch.insert(p);
                }
                let dirs_watched = watched.inner.lock().1.clone();
                let mut dirs = HashSet::new();
                let mut files = HashSet::new();
                for p in batch {
                    // FSEvents is recursive underneath; keep only direct children of watched folders
                    if let Some(parent) = p.parent().filter(|d| dirs_watched.contains(*d)) {
                        dirs.insert(parent.to_path_buf());
                        files.insert(p.clone());
                    }
                    if dirs_watched.contains(&p) {
                        dirs.insert(p);
                    }
                }
                if dirs.is_empty() {
                    continue;
                }
                tracing::debug!(event = "fs.change", dirs = dirs.len(), files = files.len());
                let (dirs, files): (Vec<_>, Vec<_>) = (dirs.into_iter().collect(), files.into_iter().collect());
                publish_change(&cache, &bus, &dirs, &files, &[]);
            }
        });
        Ok(me)
    }

    /// Replace the watched set (only the differences are applied).
    pub fn set(&self, want: HashSet<PathBuf>) {
        let mut g = self.inner.lock();
        let (w, have) = &mut *g;
        for p in have.difference(&want) {
            let _ = w.unwatch(p);
        }
        for p in want.difference(have) {
            if let Err(e) = w.watch(p, RecursiveMode::NonRecursive) {
                tracing::debug!(event = "watch.failed", path = %p.display(), error = %e);
            }
        }
        *have = want;
    }

    pub fn len(&self) -> usize {
        self.inner.lock().1.len()
    }
}

/// Folders to watch for one pane: its root, expanded folders and the folders of open tabs.
pub fn wanted(root: &str, expanded: &[String], tabs: impl Iterator<Item = String>) -> HashSet<PathBuf> {
    let mut s: HashSet<PathBuf> = HashSet::new();
    if !root.is_empty() {
        s.insert(PathBuf::from(root));
    }
    s.extend(expanded.iter().map(PathBuf::from));
    s.extend(tabs.filter_map(|t| Path::new(&t).parent().map(Path::to_path_buf)));
    s.retain(|p| p.is_dir());
    s
}
