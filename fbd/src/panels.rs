// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Which iTerm2 window each panel lives in (AC-36). A web view cannot learn its window
//! (AS-09), so panels claim one: the key window when they load (tentative), the window
//! they had before an in-page reload (restored), or the key window the bridge reads after
//! the user acted in the panel (confirmed). Two claims on one window cannot both be right.

use std::collections::{HashMap, HashSet};
use std::time::Duration;

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::Json;
use parking_lot::Mutex;
use serde::Deserialize;
use serde_json::json;
use tokio::sync::oneshot;

use crate::events::Event;
use crate::http::{err, ApiResult};
use crate::Shared;

#[derive(Clone, Copy, PartialEq, PartialOrd, Debug)]
pub enum Level {
    Tentative,
    Restored,
    Confirmed,
}

#[derive(Default)]
pub struct Panels {
    claims: Mutex<HashMap<String, (String, Level)>>,
    /// open event streams per panel: an old stream may close after the reconnected one
    streams: Mutex<HashMap<String, usize>>,
    /// windows where load guesses collided (several panels loaded while one window was
    /// key): no load guess is trusted there until a user's action settles it
    contested: Mutex<HashSet<String>>,
    asks: Mutex<(u64, HashMap<u64, oneshot::Sender<Option<String>>>)>,
}

impl Panels {
    /// Record `client`'s claim on `window`. Returns whether it holds and the other clients
    /// that lost theirs: a stronger claim wins, two tentative ones both fall, otherwise the
    /// newer one wins.
    pub fn claim(&self, client: &str, window: &str, level: Level) -> (bool, Vec<String>) {
        let mut contested = self.contested.lock();
        match level {
            Level::Tentative if contested.contains(window) => return (false, vec![]),
            Level::Confirmed => {
                contested.remove(window);
            }
            _ => {}
        }
        let mut claims = self.claims.lock();
        let rivals: Vec<(String, Level)> = claims
            .iter()
            .filter(|(c, (w, _))| c.as_str() != client && w == window)
            .map(|(c, (_, l))| (c.clone(), *l))
            .collect();
        let holds = rivals.iter().all(|(_, l)| *l < level || (*l == level && level != Level::Tentative));
        let lost: Vec<String> = rivals.into_iter().filter(|(_, l)| *l <= level).map(|(c, _)| c).collect();
        for c in &lost {
            claims.remove(c);
        }
        if level == Level::Tentative && !lost.is_empty() {
            contested.insert(window.to_string()); // two load guesses met: neither can be trusted
        }
        if holds {
            claims.insert(client.to_string(), (window.to_string(), level));
        } else {
            claims.remove(client);
        }
        (holds, lost)
    }

    /// How many panels claim each window (for /api/health).
    pub fn per_window(&self) -> HashMap<String, usize> {
        let mut n = HashMap::new();
        for (w, _) in self.claims.lock().values() {
            *n.entry(w.clone()).or_insert(0) += 1;
        }
        n
    }

    /// A claim needs an open event stream, or nothing would ever take it back.
    fn connected(&self, client: &str) -> bool {
        self.streams.lock().contains_key(client)
    }

    fn opened(&self, client: &str) {
        *self.streams.lock().entry(client.to_string()).or_insert(0) += 1;
    }

    /// One of the panel's event streams ended; with the last one its claim goes (the web
    /// view is gone, or reloading under a new client id).
    fn closed(&self, client: &str) {
        let mut s = self.streams.lock();
        let n = s.get(client).copied().unwrap_or(1).saturating_sub(1);
        if n == 0 {
            s.remove(client);
            self.claims.lock().remove(client);
        } else {
            s.insert(client.to_string(), n);
        }
    }

    fn ask(&self) -> (u64, oneshot::Receiver<Option<String>>) {
        let (tx, rx) = oneshot::channel();
        let mut a = self.asks.lock();
        a.0 += 1;
        let id = a.0;
        a.1.insert(id, tx);
        (id, rx)
    }

    fn answer(&self, id: u64, window: Option<String>) {
        if let Some(tx) = self.asks.lock().1.remove(&id) {
            let _ = tx.send(window);
        }
    }
}

/// Lives as long as one event stream of a panel; the last one to go takes the claim along.
pub struct StreamGuard(Shared, String);

impl StreamGuard {
    pub fn new(app: Shared, client: String) -> Self {
        app.panels.opened(&client);
        StreamGuard(app, client)
    }
}

impl Drop for StreamGuard {
    fn drop(&mut self) {
        self.0.panels.closed(&self.1);
    }
}

/// The asking panel, which must hold an event stream (its claim ends with it).
fn client(app: &Shared, headers: &HeaderMap) -> Result<String, axum::response::Response> {
    let c = headers
        .get("x-fb-client")
        .and_then(|v| v.to_str().ok())
        .filter(|c| !c.is_empty() && c.len() <= 64)
        .ok_or_else(|| err(StatusCode::BAD_REQUEST, "no_client", "X-FB-Client header required"))?;
    if !app.panels.connected(c) {
        return Err(err(StatusCode::CONFLICT, "not_connected", "Open the event stream (/api/events?client=…) first"));
    }
    Ok(c.to_string())
}

fn settle(app: &Shared, client: &str, window: Option<&str>, level: Level) -> ApiResult {
    let window = window.filter(|w| !w.is_empty());
    let (holds, lost) = match window {
        Some(w) => app.panels.claim(client, w, level),
        None => (false, vec![]),
    };
    tracing::info!(event = "panel.claim", client, window = window.unwrap_or("-"), level = ?level, holds, lost = lost.len());
    if !lost.is_empty() {
        app.bus.send(Event::Unbind { clients: lost });
    }
    let level = match level {
        Level::Tentative => "tentative",
        Level::Restored => "restored",
        Level::Confirmed => "confirmed",
    };
    Ok(Json(json!({"window": if holds { window } else { None }, "level": if holds { Some(level) } else { None }})).into_response())
}

#[derive(Deserialize)]
pub struct ClaimBody {
    window: Option<String>,
}

/// On load: keep the window from before an in-page reload, else claim the key window
/// (a new window's panel loads before the bridge has reported that window as key, so
/// the bridge is asked rather than the last state trusted).
pub async fn panel_claim(State(app): State<Shared>, headers: HeaderMap, Json(b): Json<ClaimBody>) -> ApiResult {
    let client = client(&app, &headers)?;
    if b.window.is_some() {
        return settle(&app, &client, b.window.as_deref(), Level::Restored);
    }
    let key = key_window(&app, &client).await?;
    settle(&app, &client, key.as_deref(), Level::Tentative)
}

/// The user acted in the panel, so its window is key.
pub async fn panel_bind(State(app): State<Shared>, headers: HeaderMap) -> ApiResult {
    let client = client(&app, &headers)?;
    let key = key_window(&app, &client).await?;
    settle(&app, &client, key.as_deref(), Level::Confirmed)
}

/// The key window as the bridge reads it after this request arrived, or None when it
/// shows no Toolbelt (no panel can live there).
async fn key_window(app: &Shared, client: &str) -> Result<Option<String>, axum::response::Response> {
    if !app.term.lock().bridge_alive() {
        return Err(err(StatusCode::SERVICE_UNAVAILABLE, "no_bridge", "iTerm2 bridge not connected"));
    }
    let (id, rx) = app.panels.ask();
    let _ = app.commands.send(json!({"action": "which-window", "req": id, "by": client}));
    match tokio::time::timeout(Duration::from_secs(3), rx).await {
        Ok(Ok(w)) => Ok(w),
        _ => {
            app.panels.answer(id, None); // drop the pending ask
            Err(err(StatusCode::GATEWAY_TIMEOUT, "no_answer", "The iTerm2 bridge did not say which window is key"))
        }
    }
}

#[derive(Deserialize)]
pub struct BoundBody {
    req: u64,
    window: Option<String>,
    #[serde(default = "yes")]
    panel: bool,
}
fn yes() -> bool {
    true
}

/// The bridge's answer to `which-window`: the key window, if it shows its Toolbelt.
pub async fn internal_bound(State(app): State<Shared>, Json(b): Json<BoundBody>) -> StatusCode {
    app.panels.answer(b.req, b.window.filter(|_| b.panel));
    StatusCode::NO_CONTENT
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn claims_settle_who_lives_where() {
        let p = Panels::default();
        assert_eq!(p.claim("a", "w1", Level::Tentative), (true, vec![]));
        assert_eq!(p.claim("b", "w2", Level::Tentative), (true, vec![]), "other windows do not compete");
        // two panels loading while w1 is key (iTerm2 restoring windows): neither is trusted
        assert_eq!(p.claim("c", "w1", Level::Tentative), (false, vec!["a".to_string()]));
        assert_eq!(p.claim("x", "w1", Level::Tentative), (false, vec![]), "nor a third one");
        assert_eq!(p.claim("x", "w1", Level::Confirmed), (true, vec![]), "a user's action settles it");
        p.forget_for_test("x");
        assert_eq!(p.claim("y", "w1", Level::Tentative), (true, vec![]), "settled: load guesses count again");
        // a restored binding is stronger than a load guess, weaker than a user's action
        assert_eq!(p.claim("d", "w2", Level::Restored), (true, vec!["b".to_string()]));
        assert_eq!(p.claim("e", "w2", Level::Tentative), (false, vec![]));
        assert!(!p.contested.lock().contains("w2"), "losing to a sure claim is no collision");
        assert_eq!(p.claim("f", "w2", Level::Confirmed), (true, vec!["d".to_string()]));
        assert_eq!(p.claim("g", "w2", Level::Confirmed), (true, vec!["f".to_string()]), "the newest action wins");
        // a panel that moves gives up its old claim
        assert_eq!(p.claim("g", "w3", Level::Confirmed), (true, vec![]));
        assert_eq!(p.claim("h", "w2", Level::Tentative), (true, vec![]));
        p.opened("h");
        p.closed("h");
        assert_eq!(p.claim("i", "w2", Level::Tentative), (true, vec![]), "a closed panel's claim is gone");
    }

    impl Panels {
        fn forget_for_test(&self, client: &str) {
            self.claims.lock().remove(client);
        }
    }

    #[test]
    fn an_old_stream_closing_late_keeps_the_claim() {
        let p = Panels::default();
        p.opened("a");                       // first stream
        p.opened("a");                       // reconnected before the old one was noticed
        assert!(p.claim("a", "w1", Level::Confirmed).0);
        p.closed("a");                       // the old stream finally goes
        assert_eq!(p.per_window().get("w1"), Some(&1), "the claim survives");
        p.closed("a");
        assert!(p.per_window().is_empty(), "the last stream takes it along");
    }

    #[tokio::test]
    async fn asks_are_answered_once() {
        let p = Panels::default();
        let (id, rx) = p.ask();
        p.answer(id, Some("w1".into()));
        p.answer(id, Some("w2".into()));
        assert_eq!(rx.await.unwrap(), Some("w1".to_string()));
    }
}
