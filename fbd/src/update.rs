// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! The update notice (AC-51): the bridge asks GitHub which release is newest and says so;
//! panels show a small chip. fbd keeps only the tag, never contacts the network, and
//! forwards the user's choices (skip a version, stop checking) to the bridge, which owns
//! them. Installing stays the user's command in a terminal (AC-40).

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::api_state::{command, no_bridge, state_of};
use crate::events::Event;
use crate::http::{err, ApiResult};
use crate::Shared;

/// `vMAJOR.MINOR.PATCH`: the only text from outside that reaches a panel.
fn is_tag(t: &str) -> bool {
    let Some(v) = t.strip_prefix('v') else { return false };
    let parts: Vec<&str> = v.split('.').collect();
    parts.len() == 3 && parts.iter().all(|p| (1..=6).contains(&p.len()) && p.bytes().all(|b| b.is_ascii_digit()))
}

/// The bridge's verdict: a newer release (`{"latest": "v0.19.0"}`) or none (`null`).
pub async fn internal_update(State(app): State<Shared>, Json(body): Json<Value>) -> StatusCode {
    let notice = body["latest"].as_str().filter(|t| is_tag(t)).map_or(Value::Null, |t| json!({"latest": t}));
    let mut t = app.term.lock();
    if t.update != notice {
        t.update = notice;
        t.version += 1;
        app.bus.send(Event::State(state_of(&t)));
    }
    StatusCode::NO_CONTENT
}

#[derive(Deserialize)]
pub struct Choice {
    /// skip (this version), off (stop checking), on (check again)
    action: String,
    version: Option<String>,
}

/// The chip's menu: the bridge stores the choice and answers with a new notice.
pub async fn choose(State(app): State<Shared>, headers: HeaderMap, Json(b): Json<Choice>) -> ApiResult {
    let valid = match b.action.as_str() {
        "skip" => b.version.as_deref().is_some_and(is_tag),
        "off" | "on" => true,
        _ => false,
    };
    if !valid {
        return Err(err(StatusCode::BAD_REQUEST, "bad_request", "skip a version, or switch checking off or on"));
    }
    if !app.term.lock().bridge_alive() {
        return Err(no_bridge());
    }
    command(&app, json!({"action": format!("update-{}", b.action), "version": b.version}), &headers);
    Ok(StatusCode::NO_CONTENT.into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_release_tags_pass() {
        for ok in ["v0.19.0", "v10.2.345"] {
            assert!(is_tag(ok), "{ok}");
        }
        for bad in ["", "v1.2", "1.2.3", "v1.2.3-rc1", "v1.2.3.4", "v1..3", "v1.2.<b>", "v1234567.0.0", "v-1.0.0", "main"] {
            assert!(!is_tag(bad), "{bad}");
        }
    }

    #[tokio::test]
    async fn the_notice_reaches_panels_once_and_only_as_a_tag() {
        let app = crate::tests::app(std::env::temp_dir().join(format!("fbd-update-{}.json", std::process::id())));
        let mut events = app.bus.subscribe();
        internal_update(State(app.clone()), Json(json!({"latest": "v0.19.0", "url": "https://evil.example"}))).await;
        let Event::State(s) = events.recv().await.unwrap() else { panic!("a state event") };
        assert_eq!(s["update"], json!({"latest": "v0.19.0"}));
        internal_update(State(app.clone()), Json(json!({"latest": "v0.19.0"}))).await;
        assert!(events.try_recv().is_err(), "the same notice is not announced again");
        internal_update(State(app.clone()), Json(json!({"latest": "<script>"}))).await;
        let Event::State(s) = events.recv().await.unwrap() else { panic!("a state event") };
        assert!(s.get("update").is_none(), "anything but a tag clears the notice");
    }

    #[tokio::test]
    async fn choices_go_to_the_bridge_validated() {
        let app = crate::tests::app(std::env::temp_dir().join(format!("fbd-update-c-{}.json", std::process::id())));
        let mut commands = app.commands.subscribe();
        let ask = |action: &str, version: Option<&str>| Json(Choice { action: action.into(), version: version.map(Into::into) });
        let h = HeaderMap::new();
        assert_eq!(choose(State(app.clone()), h.clone(), ask("skip", Some("x"))).await.unwrap_err().status(), StatusCode::BAD_REQUEST);
        assert_eq!(choose(State(app.clone()), h.clone(), ask("nuke", None)).await.unwrap_err().status(), StatusCode::BAD_REQUEST);
        assert_eq!(choose(State(app.clone()), h.clone(), ask("off", None)).await.unwrap_err().status(), StatusCode::SERVICE_UNAVAILABLE, "no bridge");
        app.term.lock().last_push = Some(std::time::Instant::now());
        assert_eq!(choose(State(app.clone()), h, ask("skip", Some("v0.19.0"))).await.unwrap().status(), StatusCode::NO_CONTENT);
        assert_eq!(commands.recv().await.unwrap(), json!({"action": "update-skip", "version": "v0.19.0", "by": null}));
    }
}
