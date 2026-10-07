// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! The web app's Files panels (AC-53) show panes that are usually not the focused one, under
//! workspace keys of their own: `web:` + the JSON array `[host, key]` (host "" for this Mac),
//! so neither part needs escaping. Their folders are watched as well as the focused pane's,
//! for as long as a web panel used them recently.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use parking_lot::Mutex;

use crate::watcher;
use crate::workspace::Pane;

/// A web panel that has not read or written its workspace for this long is not watched.
const KEEP: Duration = Duration::from_secs(15 * 60);
/// At most this many web panes are watched; a new one replaces the one used longest ago.
const MOST: usize = 16;
const PREFIX: &str = "web:";

pub fn is_web(key: &str) -> bool {
    key.starts_with(PREFIX)
}

/// The host of a web pane's files, or None for this Mac (or a key that is not a web pane's).
pub fn host_of(key: &str) -> Option<String> {
    let [host, _]: [String; 2] = serde_json::from_str(key.strip_prefix(PREFIX)?).ok()?;
    Some(host).filter(|h| !h.is_empty())
}

#[derive(Default)]
pub struct WebPanes(Mutex<HashMap<String, Instant>>);

impl WebPanes {
    /// A panel used `key`; true when it is a web pane that was not watched yet.
    pub fn touch(&self, key: &str) -> bool {
        if !is_web(key) {
            return false;
        }
        let mut g = self.0.lock();
        let new = g.insert(key.to_string(), Instant::now()).is_none();
        if g.len() > MOST {
            let oldest = g.iter().min_by_key(|(_, at)| **at).map(|(k, _)| k.clone());
            g.remove(&oldest.unwrap_or_default());
        }
        new
    }

    pub fn keys(&self) -> Vec<String> {
        let mut g = self.0.lock();
        g.retain(|_, at| at.elapsed() < KEEP);
        g.keys().cloned().collect()
    }
}

/// What one host's agent watches: one root, then more folders and the open files.
#[derive(Debug, Default, PartialEq)]
pub struct RemoteWatch {
    pub root: String,
    pub expanded: Vec<String>,
    pub files: Vec<String>,
}

/// The folders to watch here and per host, for panes given with their host (None: this Mac).
pub fn plan(panes: Vec<(Option<String>, Pane)>) -> (HashSet<PathBuf>, HashMap<String, RemoteWatch>) {
    let mut local = HashSet::new();
    let mut remote: HashMap<String, RemoteWatch> = HashMap::new();
    for (host, p) in panes {
        let files = p.tabs.into_iter().map(|t| t.path);
        let Some(h) = host else {
            local.extend(watcher::wanted(&p.root, &p.expanded, files));
            continue;
        };
        let w = remote.entry(h).or_default();
        if w.root.is_empty() { w.root = p.root } else if !p.root.is_empty() { w.expanded.push(p.root) }
        w.expanded.extend(p.expanded);
        w.files.extend(files);
    }
    (local, remote)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::Tab;

    fn pane(root: &str, expanded: &[&str], tabs: &[&str]) -> Pane {
        Pane { root: root.into(), expanded: expanded.iter().map(|s| s.to_string()).collect(),
            tabs: tabs.iter().map(|t| Tab { path: t.to_string(), view: "auto".into() }).collect(), ..Default::default() }
    }

    #[test]
    fn hosts_come_only_from_a_web_key() {
        // a host key is ssh arguments, which may hold slashes and spaces
        assert_eq!(host_of(r#"web:["-i /k/id devbox.example","tmux:devbox:/tmp/s:%1"]"#).as_deref(), Some("-i /k/id devbox.example"));
        assert_eq!(host_of(r#"web:["","w0t0p0:ABC"]"#), None);
        assert_eq!(host_of("web:not json"), None);
        assert_eq!(host_of(r#"["devbox","k"]"#), None);
        assert!(is_web(r#"web:["","k"]"#) && !is_web("w0t0p0:ABC"));
    }

    #[test]
    fn web_panes_are_watched_beside_the_focused_one() {
        // local folders are watched only if they exist
        let base = std::env::temp_dir().join(format!("fbd-watch-web-{}", std::process::id()));
        for d in ["a/src", "b", "c"] {
            std::fs::create_dir_all(base.join(d)).unwrap();
        }
        let at = |p: &str| base.join(p).to_string_lossy().into_owned();
        let (local, remote) = plan(vec![
            (None, pane(&at("a"), &[&at("a/src")], &[])),
            (None, pane(&at("b"), &[], &[&at("c/x.md")])),
            (Some("devbox.example".into()), pane("/home/alex", &["/home/alex/p"], &["/home/alex/r.txt"])),
        ]);
        for p in ["a", "a/src", "b", "c"] {
            assert!(local.contains(&base.join(p)), "{p}");
        }
        std::fs::remove_dir_all(&base).unwrap();
        assert_eq!(remote["devbox.example"], RemoteWatch { root: "/home/alex".into(),
            expanded: vec!["/home/alex/p".into()], files: vec!["/home/alex/r.txt".into()] });
    }

    #[test]
    fn a_second_pane_on_a_host_adds_its_root_as_a_folder() {
        let (_, remote) = plan(vec![(Some("devbox.example".into()), pane("/home/alex/a", &[], &[])),
            (Some("devbox.example".into()), pane("/home/alex/b", &[], &[]))]);
        assert_eq!(remote["devbox.example"].root, "/home/alex/a");
        assert_eq!(remote["devbox.example"].expanded, vec!["/home/alex/b".to_string()]);
    }

    #[test]
    fn only_web_keys_are_tracked() {
        let w = WebPanes::default();
        assert!(w.touch(r#"web:["","ID"]"#));
        assert!(!w.touch(r#"web:["","ID"]"#));
        assert!(!w.touch("w0t0p0:ID"));
        assert_eq!(w.keys(), vec![r#"web:["","ID"]"#.to_string()]);
    }

    #[test]
    fn a_signed_in_browser_cannot_grow_the_watch_without_end() {
        let w = WebPanes::default();
        for i in 0..40 {
            w.touch(&format!(r#"web:["","K{i}"]"#));
        }
        assert_eq!(w.keys().len(), MOST);
        assert!(w.keys().contains(&r#"web:["","K39"]"#.to_string()), "the newest stays");
    }
}
