// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Web access (AC-52): the bridge can serve the iTerm2 sessions and this panel to a browser
//! on the network. The bridge owns the setting and the server; fbd only shows panels its
//! status (on or off, the addresses, the last error) and forwards the panel's on/off choice.
//! fbd itself stays on 127.0.0.1.

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api_state::{command, no_bridge, state_of};
use crate::events::Event;
use crate::http::ApiResult;
use crate::Shared;

/// Short text without control or invisible characters: the bridge's words, shown in a panel.
fn plain(v: &Value, max: usize) -> Option<String> {
    v.as_str().filter(|s| s.len() <= max && !crate::termtext::has_control(s)).map(str::to_string)
}

/// An address the page can be opened at: `http(s)://host:port/`.
fn is_address(u: &str) -> bool {
    (u.starts_with("http://") || u.starts_with("https://")) && u.len() <= 200
        && u.bytes().all(|b| b.is_ascii_graphic())
}

/// The bridge's status: `{"enabled", "password", "urls", "error"}`; null when it has none.
pub fn status_of(body: &Value) -> Value {
    if !body.is_object() {
        return Value::Null;
    }
    let urls: Vec<&str> = body["urls"].as_array().map_or(vec![], |a| a.iter().filter_map(Value::as_str).filter(|u| is_address(u)).take(8).collect());
    json!({
        "enabled": body["enabled"].as_bool().unwrap_or(false),
        "password": body["password"].as_bool().unwrap_or(false),
        "urls": urls,
        "error": plain(&body["error"], 300),
    })
}

pub async fn internal_web(State(app): State<Shared>, Json(body): Json<Value>) -> StatusCode {
    let status = status_of(&body);
    let mut t = app.term.lock();
    if t.web != status {
        t.web = status;
        t.version += 1;
        app.bus.send(Event::State(state_of(&t)));
    }
    StatusCode::NO_CONTENT
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Switch {
    On,
    Off,
}

#[derive(Deserialize)]
pub struct Choice {
    action: Switch,
}

/// The panel's menu: the bridge stores the choice, starts or stops the server and reports back.
pub async fn choose(State(app): State<Shared>, headers: HeaderMap, Json(b): Json<Choice>) -> ApiResult {
    if !app.term.lock().bridge_alive() {
        return Err(no_bridge());
    }
    let action = match b.action { Switch::On => "web-on", Switch::Off => "web-off" };
    command(&app, json!({ "action": action }), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_addresses_and_plain_text_reach_panels() {
        let s = status_of(&json!({"enabled": true, "password": true, "error": "port 8765 is in use",
            "urls": ["http://10.0.0.5:8765/", "javascript:alert(1)", "http://a b/", 7]}));
        assert_eq!(s["urls"], json!(["http://10.0.0.5:8765/"]));
        assert_eq!(s["error"], json!("port 8765 is in use"));
        assert_eq!(status_of(&json!({"error": "bad\u{7}text"}))["error"], Value::Null);
        assert_eq!(status_of(&json!({"error": "a\u{202E}txt"}))["error"], Value::Null);
        assert_eq!(status_of(&json!("off")), Value::Null);
    }

    #[tokio::test]
    async fn the_status_reaches_panels_once() {
        let app = crate::tests::app(std::env::temp_dir().join(format!("fbd-web-{}.json", std::process::id())));
        let mut events = app.bus.subscribe();
        let body = json!({"enabled": true, "password": true, "urls": ["http://127.0.0.1:8765/"]});
        internal_web(State(app.clone()), Json(body.clone())).await;
        internal_web(State(app.clone()), Json(body)).await;
        let mut states = 0;
        while let Ok(e) = events.try_recv() {
            if let Event::State(s) = e {
                assert_eq!(s["web"]["urls"], json!(["http://127.0.0.1:8765/"]));
                states += 1;
            }
        }
        assert_eq!(states, 1);
    }

    #[test]
    fn only_on_and_off_are_accepted() {
        assert!(serde_json::from_value::<Choice>(json!({"action": "on"})).is_ok());
        assert!(serde_json::from_value::<Choice>(json!({"action": "password"})).is_err());
    }
}
