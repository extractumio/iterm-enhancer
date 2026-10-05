// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Recovery metadata only: no output, history, environment or arbitrary command.
use std::collections::HashSet;

use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Snapshot {
    pub version: u32,
    pub epoch: String,
    pub captured: u64,
    pub windows: Vec<Window>,
    pub servers: Vec<TmuxServer>,
    pub warnings: Vec<String>,
}

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Window {
    pub id: String,
    pub frame: Frame,
    pub fullscreen: bool,
    pub active: Option<String>,
    pub tabs: Vec<Tab>,
}

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Frame { pub origin: Point, pub size: Dimensions }
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Point { pub x: f64, pub y: f64 }
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Dimensions { pub width: f64, pub height: f64 }

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Tab {
    pub id: String,
    pub control: bool,
    pub active: Option<String>,
    pub tree: Layout,
    pub panes: Vec<Pane>,
}

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(untagged)]
pub enum Layout { Leaf(Leaf), Split(Split) }
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Leaf { pub pane: String }
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Split { pub vertical: bool, pub children: Vec<Layout> }

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Pane {
    pub id: String,
    pub profile: Option<String>,
    pub cwd: Option<String>,
    pub cwd_status: String,
    pub observed: u64,
    pub job: Option<String>,
    pub grid: Dimensions,
    pub connection: Connection,
}

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Connection {
    Shell,
    Ssh { args: Vec<String> },
    Tmux { server: String, session: String, pane: String, control: bool },
    Unsupported { reason: String },
}

#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct TmuxServer {
    pub id: String,
    pub local: bool,
    pub socket: String,
    pub args: Vec<String>,
    pub pid: u64,
    pub started: u64,
    pub sessions: Vec<TmuxSession>,
}
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct TmuxSession {
    pub id: String, pub name: String, pub created: u64, pub windows: Vec<TmuxWindow>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub group: Option<String>,
}
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct TmuxWindow { pub id: String, pub index: u32, pub layout: String, pub active: String, pub panes: Vec<TmuxPane> }
#[derive(Clone, Deserialize, Serialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct TmuxPane { pub id: String, pub cwd: Option<String>, pub job: Option<String>, pub grid: Dimensions }

fn text(s: &str, max: usize) -> bool {
    s.len() <= max && !s.chars().any(char::is_control)
}
fn path(s: &str) -> bool { s.starts_with('/') && text(s, 4096) }
fn dimensions(d: &Dimensions, max: f64) -> bool {
    d.width.is_finite() && d.height.is_finite() && d.width >= 1.0 && d.height >= 1.0 && d.width <= max && d.height <= max
}

/// Explicit connection values only; executable -o overrides and remote commands are refused.
pub fn ssh_args(args: &[String]) -> bool {
    if args.is_empty() || args.len() > 40 || !args.iter().all(|s| text(s, 4096)) { return false; }
    let mut i = 0;
    while i + 1 < args.len() {
        if matches!(args[i].as_str(), "-4" | "-6" | "-C") { i += 1; continue; }
        if !matches!(args[i].as_str(), "-p" | "-l" | "-i" | "-J" | "-F") || i + 2 >= args.len() { return false; }
        if args[i + 1].is_empty() || args[i + 1].starts_with('-') { return false; }
        let value = &args[i + 1];
        if args[i] == "-p" && !value.parse::<u16>().is_ok_and(|p| p > 0) { return false; }
        if matches!(args[i].as_str(), "-l" | "-J") && !value.chars().all(|c| c.is_ascii_alphanumeric() || "@._-:[],".contains(c)) { return false; }
        i += 2;
    }
    i == args.len() - 1 && !args[i].is_empty() && !args[i].starts_with('-') &&
        args[i].chars().all(|c| c.is_ascii_alphanumeric() || "@._-:[]".contains(c))
}

fn tmux_id(s: &str, prefix: char) -> bool {
    s.len() > 1 && s.len() <= 24 && s.starts_with(prefix) && s[1..].bytes().all(|c| c.is_ascii_digit())
}

fn tree(node: &Layout, depth: usize, ids: &mut HashSet<String>) -> bool {
    if depth > 16 { return false; }
    match node {
        Layout::Leaf(l) => text(&l.pane, 128) && !l.pane.is_empty() && ids.insert(l.pane.clone()),
        Layout::Split(s) => !s.children.is_empty() && s.children.len() <= 64 && s.children.iter().all(|c| tree(c, depth + 1, ids)),
    }
}

impl Snapshot {
    pub fn count(&self) -> usize { self.windows.iter().flat_map(|w| &w.tabs).map(|t| t.panes.len()).sum() }

    pub fn validate(&self) -> Result<(), &'static str> {
        if self.version != 1 || !text(&self.epoch, 128) || self.epoch.is_empty() { return Err("Unsupported checkpoint version or epoch"); }
        if self.windows.len() > 256 || self.count() > 2048 || self.servers.len() > 64 ||
            self.warnings.len() > 256 || !self.warnings.iter().all(|s| text(s, 512)) { return Err("Checkpoint exceeds limits"); }
        let mut all = HashSet::new();
        for w in &self.windows {
            if w.id.is_empty() || !text(&w.id, 128) || !all.insert(format!("w:{}", w.id)) || w.tabs.is_empty() || w.tabs.len() > 256 ||
                !dimensions(&w.frame.size, 32768.0) || !w.frame.origin.x.is_finite() || !w.frame.origin.y.is_finite() ||
                w.frame.origin.x.abs() > 1e6 || w.frame.origin.y.abs() > 1e6 ||
                w.active.as_ref().is_some_and(|id| !w.tabs.iter().any(|t| &t.id == id)) { return Err("Invalid checkpoint window"); }
            for t in &w.tabs {
                let mut ids = HashSet::new();
                if t.id.is_empty() || !text(&t.id, 128) || !all.insert(format!("t:{}", t.id)) || !tree(&t.tree, 0, &mut ids) ||
                    ids.len() != t.panes.len() || t.active.as_ref().is_some_and(|id| !ids.contains(id)) { return Err("Invalid checkpoint split tree"); }
                for p in &t.panes {
                    if !ids.remove(&p.id) || !all.insert(format!("p:{}", p.id)) ||
                        !dimensions(&p.grid, 4096.0) || p.cwd.as_deref().is_some_and(|s| !path(s)) ||
                        !matches!(p.cwd_status.as_str(), "known" | "stale" | "unknown") ||
                        p.profile.as_deref().is_some_and(|s| !text(s, 128)) || p.job.as_deref().is_some_and(|s| !text(s, 128)) {
                        return Err("Invalid checkpoint pane");
                    }
                    match &p.connection {
                        Connection::Ssh { args } if !ssh_args(args) => return Err("Unsafe SSH recipe"),
                        Connection::Tmux { server, session, pane, control } => {
                            if !text(server, 256) || !tmux_id(session, '$') || !tmux_id(pane, '%') || *control != t.control ||
                                !self.servers.iter().filter(|s| &s.id == server).flat_map(|s| &s.sessions)
                                    .filter(|s| &s.id == session).flat_map(|s| &s.windows).flat_map(|w| &w.panes).any(|p| &p.id == pane) {
                                return Err("Invalid tmux reference");
                            }
                        }
                        Connection::Unsupported { reason } if !text(reason, 512) => return Err("Invalid connection report"),
                        _ => {}
                    }
                }
            }
        }
        for s in &self.servers {
            if !text(&s.id, 256) || !all.insert(format!("server:{}", s.id)) || !path(&s.socket) ||
                (!s.local && !ssh_args(&s.args)) || (s.local && !s.args.is_empty()) || s.sessions.len() > 256 { return Err("Invalid tmux server"); }
            let mut sessions = HashSet::new();
            for session in &s.sessions {
                if !tmux_id(&session.id, '$') || !sessions.insert(&session.id) || !text(&session.name, 128) || session.windows.is_empty() || session.windows.len() > 256 { return Err("Invalid tmux session"); }
                if session.group.as_deref().is_some_and(|g| g.is_empty() || !text(g, 128)) {
                    return Err("Invalid tmux group");
                }
                if session.group.is_some() && s.sessions.iter().filter(|other| other.group == session.group).any(|other| {
                    other.windows.len() != session.windows.len() || session.windows.iter().any(|w| {
                        !other.windows.iter().any(|ow| ow.id == w.id && ow.index == w.index && ow.layout == w.layout &&
                            ow.panes.len() == w.panes.len() && w.panes.iter().all(|p| ow.panes.iter().any(|op| op.id == p.id &&
                                op.cwd == p.cwd && op.job == p.job && op.grid.width == p.grid.width && op.grid.height == p.grid.height)))
                    })
                }) { return Err("Inconsistent tmux group"); }
                let mut windows = HashSet::new();
                let mut indexes = HashSet::new();
                for w in &session.windows {
                    if !tmux_id(&w.id, '@') || !windows.insert(&w.id) || !indexes.insert(w.index) || !tmux_id(&w.active, '%') ||
                        !w.panes.iter().any(|p| p.id == w.active) || w.panes.is_empty() || w.panes.len() > 256 ||
                        w.layout.len() > 16384 || !w.layout.chars().all(|c| c.is_ascii_hexdigit() || ",x{}[]".contains(c)) {
                        return Err("Invalid tmux layout");
                    }
                    let mut panes = HashSet::new();
                    for p in &w.panes {
                        if !tmux_id(&p.id, '%') || !panes.insert(&p.id) || !dimensions(&p.grid, 4096.0) ||
                            p.cwd.as_deref().is_some_and(|s| !path(s)) || p.job.as_deref().is_some_and(|s| !text(s, 128)) { return Err("Invalid tmux pane"); }
                    }
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn executable_ssh_options_and_commands_are_not_recipes() {
        let a = |s: &[&str]| s.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(ssh_args(&a(&["-p", "2222", "-J", "jump.example", "devbox.example"])));
        for bad in [vec!["-o", "ProxyCommand=app", "host"], vec!["host", "coding-agent"], vec!["-p", "host"], vec!["host\napp"]] {
            assert!(!ssh_args(&a(&bad)));
        }
    }

    #[test]
    fn group_requires_an_authoritative_consistent_graph_and_legacy_stays_ungrouped() {
        use serde_json::json;
        let session = json!({"id":"$0","name":"owned","created":1,"group":"owned","windows":[{
            "id":"@0","index":0,"layout":"b25d,80x24,0,0,0","active":"%0","panes":[{
                "id":"%0","cwd":"/tmp","job":"sh","grid":{"width":80,"height":24}}]}]});
        let mut other = session.clone(); other["id"] = json!("$1");
        let value = json!({"version":1,"epoch":"run","captured":1,"windows":[],"warnings":[],"servers":[{
            "id":"tmux:localhost:/tmp/test","local":true,"socket":"/tmp/test","args":[],"pid":1,"started":1,
            "sessions":[session,other]}]});
        let decode = |v| serde_json::from_value::<Snapshot>(v).unwrap();
        assert!(decode(value.clone()).validate().is_ok());
        for (field, changed) in [("index", json!(1)), ("layout", json!("0000,90x24,0,0,0"))] {
            let mut bad = value.clone(); bad["servers"][0]["sessions"][1]["windows"][0][field] = changed;
            assert_eq!(decode(bad).validate(), Err("Inconsistent tmux group"));
        }
        for (field, changed) in [("cwd", json!("/elsewhere")), ("job", json!("app")), ("grid", json!({"width":90,"height":24}))] {
            let mut bad = value.clone(); bad["servers"][0]["sessions"][1]["windows"][0]["panes"][0][field] = changed;
            assert_eq!(decode(bad).validate(), Err("Inconsistent tmux group"));
        }
        for group in ["", "invalid\ngroup"] {
            let mut bad = value.clone(); bad["servers"][0]["sessions"][0]["group"] = json!(group);
            assert_eq!(decode(bad).validate(), Err("Invalid tmux group"));
        }
        let mut legacy = value;
        for s in legacy["servers"][0]["sessions"].as_array_mut().unwrap() {
            s.as_object_mut().unwrap().remove("group");
        }
        let legacy = decode(legacy);
        assert!(legacy.validate().is_ok());
        assert!(legacy.servers[0].sessions.iter().all(|s| s.group.is_none()));
    }
}
