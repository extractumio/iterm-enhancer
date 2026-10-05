// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Observed closures overlay immutable history, including a pinned recovery source.
use std::collections::HashSet;

use crate::checkpoint::{Connection, Layout, Snapshot};
use crate::recovery_store::{Entry, Store};

fn project(node: Layout, retired: &HashSet<String>) -> Option<Layout> {
    match node {
        Layout::Leaf(leaf) => (!retired.contains(&leaf.pane)).then_some(Layout::Leaf(leaf)),
        Layout::Split(mut split) => {
            split.children = split.children.into_iter().filter_map(|c| project(c, retired)).collect();
            match split.children.len() {
                0 => None,
                1 => split.children.pop(),
                _ => Some(Layout::Split(split)),
            }
        }
    }
}

impl Store {
    pub fn restorable(&self, selected: &str) -> Result<Snapshot, String> {
        let mut snapshot = self.snapshot(selected)?;
        let affected: HashSet<_> = snapshot.windows.iter().flat_map(|w| &w.tabs).flat_map(|t| &t.panes)
            .filter(|p| self.index.retired.contains(&p.id)).filter_map(|p| match &p.connection {
                Connection::Tmux { server, control: true, .. } => Some(server.clone()),
                _ => None,
            }).collect();
        snapshot.windows.retain_mut(|window| {
            window.tabs.retain_mut(|tab| {
                tab.panes.retain(|p| !self.index.retired.contains(&p.id));
                for pane in &mut tab.panes {
                    if matches!(&pane.connection, Connection::Tmux { server, .. } if affected.contains(server)) {
                        pane.connection = Connection::Unsupported { reason:
                            "Closed tmux control pane; recorded server graph is not recreated".into() };
                    }
                }
                let Some(tree) = project(tab.tree.clone(), &self.index.retired) else { return false };
                tab.tree = tree;
                if tab.active.as_ref().is_some_and(|id| !tab.panes.iter().any(|p| &p.id == id)) {
                    tab.active = tab.panes.first().map(|p| p.id.clone());
                }
                true
            });
            if window.active.as_ref().is_some_and(|id| !window.tabs.iter().any(|t| &t.id == id)) {
                window.active = window.tabs.first().map(|t| t.id.clone());
            }
            !window.tabs.is_empty()
        });
        snapshot.validate().map_err(str::to_string)?;
        Ok(snapshot)
    }

    pub(crate) fn lifecycle_refs(&self, entries: &[Entry]) -> Result<HashSet<String>, String> {
        let mut refs = HashSet::new();
        for entry in entries {
            for pane in self.snapshot(&entry.id)?.windows.into_iter().flat_map(|w| w.tabs).flat_map(|t| t.panes) {
                refs.insert(pane.id);
            }
        }
        if let Some(job) = &self.job {
            refs.extend(job.steps.values().filter_map(|s| s.session.clone()));
        }
        Ok(refs)
    }

    pub fn lifecycle(&mut self, epoch: &str, sid: String, marker: Option<&str>, revive: bool) -> Result<(), String> {
        self.writable()?;
        if epoch != self.index.epoch || epoch.is_empty() { return Err("Session event belongs to a different iTerm2 process".into()); }
        if sid.is_empty() || sid.len() > 128 || sid.chars().any(char::is_control) {
            return Err("Invalid session identity".into());
        }
        if revive && self.index.retired.is_empty() { return Ok(()); }
        let mut identities = HashSet::from([sid.clone()]);
        if let Some(job) = &self.job {
            let source = self.snapshot(&job.snapshot)?;
            let source_ids: HashSet<_> = source.windows.iter().flat_map(|w| &w.tabs).flat_map(|t| &t.panes).map(|p| p.id.as_str()).collect();
            identities.extend(job.steps.iter().filter(|(old, step)| step.session.as_ref() == Some(&sid) && source_ids.contains(old.as_str())).map(|(old, _)| old.clone()));
        }
        // Creation markers remain valid across later jobs, but only for retained,
        // validated sources; a foreign marker cannot invent a source identity.
        if let Some((selected, old)) = marker.and_then(|m| m.strip_prefix("File Browser Restore ")).and_then(|m| m.split_once(':')) {
            if self.index.entries.iter().any(|e| e.id == selected) && self.snapshot(selected)?.windows.iter()
                .flat_map(|w| &w.tabs).flat_map(|t| &t.panes).any(|p| p.id == old) {
                identities.insert(old.to_string());
            }
        }
        if revive && identities.is_disjoint(&self.index.retired) { return Ok(()); }
        if !revive && identities.is_subset(&self.index.retired) { return Ok(()); }
        let refs = self.lifecycle_refs(&self.index.entries)?;
        let mut index = self.index.clone();
        index.retired.retain(|id| refs.contains(id));
        if revive { for id in identities { index.retired.remove(&id); } }
        else { index.retired.extend(identities); }
        if index.retired.len() > 16384 { return Err("Session closure history exceeds limits; previous state preserved".into()); }
        if index.retired == self.index.retired { return Ok(()); }
        self.commit_index(index)
    }
}
