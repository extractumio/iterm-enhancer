// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Server-sent events fan-out: one broadcast channel, every panel subscribes.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::broadcast;

use crate::files::etag;
use crate::listing::Cache;

/// A changed file and its etag now (`None`: gone), so panels need no request to decide.
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Stamp {
    pub path: String,
    pub etag: Option<String>,
}

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Moved {
    pub from: String,
    pub to: String,
}

#[derive(Clone, Serialize, Debug)]
#[serde(tag = "type", rename_all = "kebab-case")]
pub enum Event {
    /// terminal state pushed by the bridge (focused pane, cwd, theme)
    State(Value),
    /// a pane's workspace changed; `by` is the panel that wrote it (it ignores its own echo)
    Workspace { key: String, rev: u64, by: Option<String> },
    /// folders or files changed on disk; renames done through a panel arrive as `moved`
    FsChange {
        dirs: Vec<String>,
        files: Vec<Stamp>,
        moved: Vec<Moved>,
        /// the remote host these paths are on (AC-37); absent for the Mac's own files
        #[serde(skip_serializing_if = "Option::is_none")]
        host: Option<String>,
    },
    /// open `path` as a tab in the viewer window that is already open
    ViewerOpen { path: String, host: Option<String> },
    /// The upstream stream reconnected: reconcile files without replacing dirty buffers.
    Rescan { host: Option<String> },
    /// a bridge command failed; only the panel `by` (its `X-FB-Client`) shows it
    BridgeError { message: String, by: Option<String> },
    /// these panels lost their window binding to a stronger or competing claim (AC-36)
    Unbind { clients: Vec<String> },
}

impl Event {
    pub fn name(&self) -> &'static str {
        match self {
            Event::State(_) => "state",
            Event::Workspace { .. } => "workspace",
            Event::FsChange { .. } => "fs-change",
            Event::ViewerOpen { .. } => "viewer-open",
            Event::Rescan { .. } => "rescan",
            Event::BridgeError { .. } => "bridge-error",
            Event::Unbind { .. } => "unbind",
        }
    }

    /// SSE payload: the bare state object, or the tagged event.
    pub fn data(&self) -> String {
        match self {
            Event::State(v) => v.to_string(),
            other => serde_json::to_string(other).unwrap_or_default(),
        }
    }
}

#[derive(Clone)]
pub struct Bus(broadcast::Sender<Event>);

impl Bus {
    pub fn new() -> Self {
        Bus(broadcast::channel(256).0)
    }
    pub fn send(&self, e: Event) {
        let _ = self.0.send(e); // no subscribers is fine
    }
    pub fn subscribe(&self) -> broadcast::Receiver<Event> {
        self.0.subscribe()
    }
    pub fn subscribers(&self) -> usize {
        self.0.receiver_count()
    }
}

/// The one way a change reaches panels (FSEvents batch or a panel's own operation):
/// forget the folders' listings, stamp the files with their current etag, broadcast.
pub fn publish_change(cache: &Arc<Cache>, bus: &Bus, dirs: &[PathBuf], files: &[PathBuf], moved: &[(PathBuf, PathBuf)]) {
    for d in dirs {
        cache.invalidate(d);
    }
    let text = |p: &Path| p.display().to_string();
    bus.send(Event::FsChange {
        dirs: dirs.iter().map(|d| text(d)).collect(),
        files: files.iter().map(|f| Stamp { path: text(f), etag: std::fs::metadata(f).ok().map(|m| etag(&m)) }).collect(),
        moved: moved.iter().map(|(f, t)| Moved { from: text(f), to: text(t) }).collect(),
        host: None,
    });
}
