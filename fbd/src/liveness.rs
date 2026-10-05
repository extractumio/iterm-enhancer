// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Authoritative identities expire caches, never renew focused-follower health.
use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::http::StatusCode;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::{api_state, events::Event, Shared};

const GRACE: Duration = Duration::from_secs(60);
const GAP: Duration = Duration::from_secs(15);

#[derive(Default)]
pub struct Live {
    last: Option<Instant>,
    windows: HashSet<String>,
    absent: HashMap<String, Instant>,
}

impl Live {
    pub fn allows_window(&self, window: &str) -> bool {
        self.last.is_none_or(|t| t.elapsed() > GAP) || self.windows.contains(window)
    }

    fn update(&mut self, b: &Inventory, candidates: HashSet<String>, at: Instant) -> HashSet<String> {
        if self.last.is_none_or(|t| at.duration_since(t) > GAP) {
            self.absent.clear();
        }
        self.last = Some(at);
        self.windows = b.windows.clone();
        let live: HashSet<String> = b.windows.iter().map(|w| format!("w:{w}"))
            .chain(b.sessions.iter().map(|s| format!("s:{s}"))).collect();
        self.absent.retain(|k, _| candidates.contains(k) && !live.contains(k));
        let mut expired = HashSet::new();
        for k in candidates.difference(&live) {
            if at.duration_since(*self.absent.entry(k.clone()).or_insert(at)) >= GRACE {
                expired.insert(k.clone());
            }
        }
        expired
    }
}

#[derive(Deserialize)]
pub struct Inventory {
    windows: HashSet<String>,
    sessions: HashSet<String>,
    keys: HashSet<String>,
    protect_tmux: bool,
}

pub async fn inventory(State(app): State<Shared>, Json(b): Json<Inventory>) -> StatusCode {
    let mut t = app.term.lock();
    let mut candidates: HashSet<String> = t.windows.keys().chain(app.panels.windows().iter())
        .map(|w| format!("w:{w}")).collect();
    for (s, _) in t.windows.values() {
        if let Some(id) = s["session"].as_str() {
            candidates.insert(format!("s:{id}"));
        }
    }
    let expired = app.live.lock().update(&b, candidates, Instant::now());
    let dead: HashSet<String> = expired.iter().filter_map(|k| k.strip_prefix("w:").map(str::to_string)).collect();
    let before = t.windows.len();
    t.windows.retain(|w, (s, _)| !dead.contains(w) &&
        !s["session"].as_str().is_some_and(|id| expired.contains(&format!("s:{id}"))));
    let focused_gone = t.state["window"].as_str().is_some_and(|w| dead.contains(w)) ||
        t.state["session"].as_str().is_some_and(|s| expired.contains(&format!("s:{s}")));
    if focused_gone {
        t.state = json!({});
    }
    let changed = before != t.windows.len() || focused_gone;
    if changed {
        t.version += 1;
        app.bus.send(Event::State(api_state::state_of(&t)));
    }
    drop(t);
    let lost = app.panels.purge_windows(&dead);
    if !lost.is_empty() {
        app.bus.send(Event::Unbind { clients: lost });
    }
    if changed {
        app.refresh_watch();
    }
    // Pure metadata maintenance: no last_push or announced_alive update here.
    app.store.maintain(&b.keys, b.protect_tmux);
    StatusCode::NO_CONTENT
}

pub async fn setup(State(app): State<Shared>, Json(value): Json<Value>) -> StatusCode {
    let mut t = app.term.lock();
    if t.setup != value {
        t.setup = value;
        t.version += 1;
        app.bus.send(Event::State(api_state::state_of(&t)));
    }
    StatusCode::NO_CONTENT
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inventory() -> Inventory {
        Inventory { windows: HashSet::from(["w2".into()]), sessions: HashSet::from(["p2".into()]),
            keys: HashSet::from(["p2".into()]), protect_tmux: false }
    }

    #[test]
    fn complete_absence_grace_resets_across_gaps() {
        let mut l = Live::default();
        let start = Instant::now();
        let candidates = HashSet::from(["w:w1".into(), "w:w2".into(), "s:p1".into()]);
        for second in (0..60).step_by(5) {
            assert!(l.update(&inventory(), candidates.clone(), start + Duration::from_secs(second)).is_empty());
        }
        assert_eq!(l.update(&inventory(), candidates.clone(), start + GRACE),
            HashSet::from(["w:w1".into(), "s:p1".into()]));
        // No inventory for 20 seconds: never count uncertainty as absence.
        assert!(l.update(&inventory(), candidates.clone(), start + Duration::from_secs(80)).is_empty());
        let mut b = inventory();
        b.windows.insert("w1".into());
        b.sessions.insert("p1".into());
        assert!(l.update(&b, candidates.clone(), start + Duration::from_secs(85)).is_empty());
        assert!(l.update(&inventory(), candidates, start + Duration::from_secs(90)).is_empty());
    }

    #[tokio::test]
    async fn purge_removes_state_claims_and_focus_without_renewing_health() {
        let path = std::env::temp_dir().join(format!("fbd-live-{}.json", std::process::id()));
        let app = crate::tests::app(path.clone());
        let state = json!({"window": "w1", "session": "p1", "key": "p1", "cwd": "/tmp"});
        api_state::internal_state(State(app.clone()), Json(state)).await;
        app.panels.claim("panel", "w1", crate::panels::Level::Confirmed);
        let old = Instant::now() - Duration::from_secs(11);
        app.term.lock().last_push = Some(old);
        inventory_handler_for_test(&app).await;
        assert_eq!(app.term.lock().last_push, Some(old), "inventory cannot hide a stalled follower");
        assert!(!app.term.lock().bridge_alive());
        // Advance the grace clock rather than sleeping a minute.
        let at = Instant::now();
        app.live.lock().absent.values_mut().for_each(|t| *t = at - GRACE);
        inventory_handler_for_test(&app).await;
        assert!(app.term.lock().windows.is_empty());
        assert!(app.term.lock().state["key"].is_null());
        assert!(app.panels.per_window().is_empty());
        assert_eq!(app.store.len(), 1, "recoverable Files state retains its TTL");
        let _ = std::fs::remove_file(path);
    }

    async fn inventory_handler_for_test(app: &Shared) {
        super::inventory(State(app.clone()), Json(inventory())).await;
    }
}
