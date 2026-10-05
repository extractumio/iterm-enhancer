// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Immutable terminal generations, protected run history and a durable recovery journal.
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::os::unix::fs::OpenOptionsExt;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::checkpoint::Snapshot;

const HISTORY: usize = 64;
const MAX_BYTES: u64 = 128 << 20;

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub id: String,
    pub epoch: String,
    pub captured: u64,
    pub windows: usize,
    pub panes: usize,
    pub bytes: u64,
    pub stable: bool,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Index {
    pub version: u32,
    pub enabled: bool,
    pub epoch: String,
    pub entries: Vec<Entry>,
    #[serde(default)]
    pub startup: Option<Startup>,
    #[serde(default)]
    pub retired: HashSet<String>,
    #[serde(default)]
    pub clean_exit: Option<String>,
}
impl Default for Index {
    fn default() -> Self { Self { version: 1, enabled: true, epoch: String::new(), entries: vec![], startup: None, retired: HashSet::new(), clean_exit: None } }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Startup {
    pub epoch: String,
    pub snapshot: Option<String>,
    pub phase: String,
    #[serde(default)]
    pub skipped: bool,
}

impl Startup {
    pub fn pending(&self) -> bool { self.phase == "pending" }
    fn valid(&self, index: &Index) -> bool {
        self.epoch == index.epoch && !self.epoch.is_empty() && self.epoch.len() <= 128 &&
            !self.epoch.chars().any(char::is_control) && matches!(self.phase.as_str(), "pending" | "done") &&
            (!self.pending() || self.snapshot.is_some()) && self.snapshot.as_ref().is_none_or(|selected|
                id(selected) && index.entries.iter().any(|e| &e.id == selected)) && (!self.skipped || !self.pending())
    }
}

#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Step {
    pub state: String,
    pub message: String,
    pub session: Option<String>,
    pub window: Option<String>,
    pub tab: Option<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Job {
    pub id: String,
    pub snapshot: String,
    #[serde(default)]
    pub epoch: Option<String>,
    pub status: String,
    pub steps: HashMap<String, Step>,
}

impl Job {
    fn valid(&self) -> bool {
        let text = |s: &str, max| s.len() <= max && !s.chars().any(char::is_control);
        id(&self.id) && id(&self.snapshot) && matches!(self.status.as_str(), "running" | "complete" | "interrupted") &&
            self.epoch.as_deref().is_none_or(|s| !s.is_empty() && text(s, 128)) &&
            self.steps.len() <= 4096 && self.steps.iter().all(|(k, s)| text(k, 512) && text(&s.message, 512) &&
                matches!(s.state.as_str(), "pending" | "restored" | "adopted" | "deviation" | "failed") &&
                [&s.session, &s.window, &s.tab].iter().all(|v| v.as_deref().is_none_or(|v| text(v, 128))))
    }
}

pub struct Store {
    dir: PathBuf,
    pub index: Index,
    pub job: Option<Job>,
    pub error: Option<String>,
    pub revision: u64,
    pub instance: String,
    blocked: bool,
    fingerprint: Option<Value>,
    unchanged_since: Instant,
}

fn atomic(path: &Path, value: &impl Serialize) -> io::Result<()> {
    let tmp = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec(value)?;
    let mut file = OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    fs::rename(&tmp, path)?;
    File::open(path.parent().expect("state path has parent"))?.sync_all()
}

fn read(path: &Path, max: u64) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    File::open(path)?.take(max + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > max { return Err(io::Error::other("Recovery file exceeds limits")); }
    Ok(bytes)
}

pub fn id(s: &str) -> bool { s.len() == 16 && s.bytes().all(|c| c.is_ascii_hexdigit()) }

fn fingerprint(snapshot: &Snapshot) -> Value {
    let mut s = snapshot.clone();
    s.captured = 0;
    for p in s.windows.iter_mut().flat_map(|w| &mut w.tabs).flat_map(|t| &mut t.panes) { p.observed = 0; }
    serde_json::to_value(s).expect("checkpoint serializes")
}

impl Store {
    pub fn load(dir: PathBuf) -> Self {
        let mut error = None;
        let index: Index = match read(&dir.join("index.json"), 4 << 20) {
            Ok(bytes) => match serde_json::from_slice::<Index>(&bytes) {
                Ok(i) if bytes.len() <= 4 << 20 && i.version == 1 && i.entries.len() <= HISTORY &&
                    i.retired.len() <= 16384 && i.retired.iter().all(|id| !id.is_empty() && id.len() <= 128 && !id.chars().any(char::is_control)) &&
                    i.entries.iter().all(|e| id(&e.id) && e.bytes <= 4 << 20 && e.epoch.len() <= 128) &&
                    i.clean_exit.as_ref().is_none_or(|epoch| epoch == &i.epoch && !epoch.is_empty() && epoch.len() <= 128 && !epoch.chars().any(char::is_control)) &&
                    i.startup.as_ref().is_none_or(|s| s.valid(&i)) => i,
                _ => { error = Some("Recovery index unreadable; checkpoint files preserved".into()); Index::default() }
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => Index::default(),
            Err(_) => { error = Some("Recovery index unavailable; checkpoint files preserved".into()); Index::default() }
        };
        let job = match read(&dir.join("job.json"), 1 << 20) {
            Ok(b) => match serde_json::from_slice::<Job>(&b) {
                Ok(j) if b.len() <= 1 << 20 && j.valid() => Some(j),
                _ => { error = Some("Recovery journal unreadable; existing terminals and files preserved".into()); None }
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound => None,
            Err(_) => { error = Some("Recovery journal unavailable; files preserved".into()); None }
        };
        let blocked = error.is_some();
        let mut store = Self { dir, index, job, error, blocked, revision: 0, instance: crate::auth::random_hex::<8>(),
                              fingerprint: None, unchanged_since: Instant::now() };
        if let Some(e) = store.index.entries.last() {
            if let Ok(s) = store.snapshot(&e.id) { store.fingerprint = Some(fingerprint(&s)); }
        }
        store
    }

    fn write_index(&self, index: &Index) -> io::Result<()> {
        if serde_json::to_vec(index).map_err(io::Error::other)?.len() > 4 << 20 {
            return Err(io::Error::other("Recovery index exceeds limits"));
        }
        fs::create_dir_all(&self.dir)?;
        atomic(&self.dir.join("index.json"), index)
    }

    pub(crate) fn commit_index(&mut self, index: Index) -> Result<(), String> {
        self.writable()?;
        self.write_index(&index).map_err(|_| "Could not commit automatic terminal recovery")?;
        self.index = index;
        self.error = None;
        self.revision += 1;
        Ok(())
    }

    pub fn enabled(&mut self, enabled: bool) -> Result<(), String> {
        self.writable()?;
        let mut index = self.index.clone();
        index.enabled = enabled;
        self.write_index(&index).map_err(|_| "Could not save recovery settings")?;
        self.index = index;
        self.revision += 1;
        Ok(())
    }

    pub fn writable(&self) -> Result<(), String> {
        if self.blocked { Err(self.error.clone().unwrap_or_else(|| "Recovery storage unavailable".into())) } else { Ok(()) }
    }

    pub fn epoch(&mut self, epoch: String) -> Result<(), String> {
        self.writable()?;
        if epoch.is_empty() || epoch.len() > 128 || epoch.chars().any(char::is_control) { return Err("Invalid recovery epoch".into()); }
        if self.index.epoch == epoch { return Ok(()); }
        if self.index.startup.is_some() { return Err("Run identity is managed by startup recovery".into()); }
        let mut index = self.index.clone(); index.epoch = epoch;
        index.clean_exit = None;
        self.write_index(&index).map_err(|_| "Could not save recovery run identity")?;
        self.index = index;
        self.revision += 1;
        Ok(())
    }

    pub fn snapshot(&self, selected: &str) -> Result<Snapshot, String> {
        if !id(selected) || !self.index.entries.iter().any(|e| e.id == selected) { return Err("Checkpoint unavailable".into()); }
        let bytes = read(&self.dir.join(format!("{selected}.json")), 4 << 20).map_err(|_| "Checkpoint unavailable or exceeds limits")?;
        if bytes.len() > 4 << 20 { return Err("Checkpoint exceeds limits".into()); }
        let snapshot: Snapshot = serde_json::from_slice(&bytes).map_err(|_| "Checkpoint unreadable; preserved on disk")?;
        snapshot.validate().map_err(str::to_string)?;
        Ok(snapshot)
    }

    pub fn capture(&mut self, snapshot: Snapshot, force: bool) -> Result<Option<String>, String> {
        self.writable()?;
        if self.index.startup.as_ref().is_some_and(Startup::pending) {
            return Err("Startup recovery pending; checkpoint capture paused".into());
        }
        if self.index.startup.as_ref().is_some_and(|s| s.epoch != snapshot.epoch) {
            return Err("Checkpoint belongs to a different iTerm2 process".into());
        }
        if self.job.as_ref().is_some_and(|j| j.status == "running") { return Err("Recovery is running; checkpoint capture paused".into()); }
        snapshot.validate().map_err(str::to_string)?;
        let bytes = serde_json::to_vec(&snapshot).map_err(|_| "Checkpoint serialization failed")?.len() as u64;
        if bytes > 4 << 20 { return Err("Checkpoint exceeds limits".into()); }
        let fp = fingerprint(&snapshot);
        if self.fingerprint.as_ref() == Some(&fp) {
            if !self.index.retired.is_empty() {
                let refs = self.lifecycle_refs(&self.index.entries)?;
                let mut index = self.index.clone();
                index.retired.retain(|id| refs.contains(id));
                if index.retired != self.index.retired { self.commit_index(index)?; }
            }
            if (force || self.unchanged_since.elapsed() >= Duration::from_secs(30)) &&
                self.index.entries.last().is_some_and(|e| !e.stable) {
                let mut index = self.index.clone();
                index.entries.last_mut().unwrap().stable = true;
                self.write_index(&index).map_err(|_| "Could not save checkpoint stability")?;
                self.index = index;
                self.revision += 1;
            }
            if self.error.take().is_some() { self.revision += 1; }
            return Ok(None);
        }
        let selected = crate::auth::random_hex::<8>();
        let mut index = self.index.clone();
        if index.epoch != snapshot.epoch { index.clean_exit = None; }
        index.epoch = snapshot.epoch.clone();
        index.entries.push(Entry { id: selected.clone(), epoch: snapshot.epoch.clone(), captured: snapshot.captured,
            windows: snapshot.windows.len(), panes: snapshot.count(), bytes, stable: force });
        // Protect one stable/nonempty generation from each of the last eight epochs.
        let epochs: Vec<String> = index.entries.iter().rev().map(|e| e.epoch.clone()).fold(Vec::new(), |mut v, e| {
            if !v.contains(&e) && v.len() < 8 { v.push(e); } v
        });
        let mut protected: HashSet<String> = epochs.iter().filter_map(|epoch|
            index.entries.iter().rev().find(|e| &e.epoch == epoch && e.windows > 0 && e.stable)
                .or_else(|| index.entries.iter().rev().find(|e| &e.epoch == epoch && e.windows > 0))
                .map(|e| e.id.clone())).collect();
        if let Some(j) = &self.job { protected.insert(j.snapshot.clone()); }
        if let Some(s) = &self.index.startup {
            if let Some(source) = &s.snapshot { protected.insert(source.clone()); }
        }
        protected.insert(selected.clone()); // latest state, including intentional empty
        let mut removed = vec![];
        while index.entries.len() > HISTORY || index.entries.iter().map(|e| e.bytes).sum::<u64>() > MAX_BYTES {
            let Some(i) = index.entries.iter().position(|e| !protected.contains(&e.id)) else { break };
            removed.push(index.entries.remove(i).id);
        }
        if !index.retired.is_empty() {
            let retained: Vec<_> = index.entries.iter().filter(|e| e.id != selected).cloned().collect();
            let mut refs = self.lifecycle_refs(&retained)?;
            refs.extend(snapshot.windows.iter().flat_map(|w| &w.tabs).flat_map(|t| &t.panes).map(|p| p.id.clone()));
            index.retired.retain(|id| refs.contains(id));
        }
        fs::create_dir_all(&self.dir).map_err(|_| "Could not create checkpoint folder")?;
        // Failed index commits may leave orphan immutable files. Bound their physical
        // bytes too, then remove them only after a successful index commit.
        if self.physical_bytes()?.saturating_add(bytes) > MAX_BYTES + (4 << 20) {
            // Reestablish the durable index before collecting failed-commit orphans.
            // If storage remains unavailable, every previous file is preserved.
            self.write_index(&self.index).map_err(|_| "Could not recommit checkpoint index; previous files preserved")?;
            self.gc();
            if self.physical_bytes()?.saturating_add(bytes) > MAX_BYTES + (4 << 20) {
                return Err("Checkpoint disk quota reached; previous files preserved".into());
            }
        }
        atomic(&self.dir.join(format!("{selected}.json")), &snapshot).map_err(|_| "Could not write checkpoint; previous generation preserved")?;
        self.write_index(&index).map_err(|_| "Could not commit checkpoint; previous generation preserved")?;
        self.index = index;
        self.revision += 1;
        self.fingerprint = Some(fp);
        self.unchanged_since = Instant::now();
        self.error = None;
        for old in removed { let _ = fs::remove_file(self.dir.join(format!("{old}.json"))); }
        self.gc();
        Ok(Some(selected))
    }

    fn gc(&self) {
        if let Ok(files) = fs::read_dir(&self.dir) {
            for file in files.flatten() {
                let name = file.file_name();
                if let Some(n) = name.to_str().and_then(|n| n.strip_suffix(".json")) {
                    if id(n) && !self.index.entries.iter().any(|e| e.id == n) {
                        let _ = fs::remove_file(file.path());
                    }
                }
            }
        }
    }

    fn physical_bytes(&self) -> Result<u64, String> {
        Ok(fs::read_dir(&self.dir).map_err(|_| "Checkpoint folder unavailable")?
            .filter_map(Result::ok).filter(|e| e.file_name().to_str().is_some_and(|n| n.ends_with(".json") && id(n.trim_end_matches(".json"))))
            .filter_map(|e| e.metadata().ok()).map(|m| m.len()).sum())
    }

    pub fn recommended(&self) -> Option<String> {
        let previous = self.index.entries.iter().rev().filter(|e| e.epoch != self.index.epoch && e.windows > 0);
        previous.clone().find(|e| e.stable).or_else(|| previous.into_iter().next())
            .or_else(|| self.index.entries.iter().rev().find(|e| e.windows > 0 && e.stable))
            .or_else(|| self.index.entries.iter().rev().find(|e| e.windows > 0)).map(|e| e.id.clone())
    }

    pub fn job(&mut self, job: Job) -> Result<(), String> {
        self.writable()?;
        if !job.valid() || serde_json::to_vec(&job).map_or(true, |b| b.len() > 1 << 20) {
            return Err("Recovery job exceeds limits".into());
        }
        fs::create_dir_all(&self.dir).map_err(|_| "Could not create recovery folder")?;
        atomic(&self.dir.join("job.json"), &job).map_err(|_| "Could not save recovery progress")?;
        self.job = Some(job);
        self.revision += 1;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn empty(epoch: &str) -> Snapshot {
        Snapshot { version: 1, epoch: epoch.into(), captured: 1, windows: vec![], servers: vec![], warnings: vec![] }
    }
    #[test]
    fn immutable_generations_ignore_observation_time_and_survive_failed_commit() {
        let dir = std::env::temp_dir().join(format!("fbd-recovery-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let mut s = Store::load(dir.clone());
        let first = s.capture(empty("run-a"), true).unwrap().unwrap();
        let mut same = empty("run-a"); same.captured = 100;
        assert!(s.capture(same, true).unwrap().is_none());
        assert_eq!(s.index.entries.len(), 1);
        assert_eq!(s.snapshot(&first).unwrap().captured, 1);
        let saved = fs::read(dir.join("index.json")).unwrap();
        fs::remove_file(dir.join("index.json")).unwrap();
        fs::create_dir(dir.join("index.json")).unwrap();
        assert!(s.capture(empty("run-b"), true).is_err());
        assert_eq!(s.index.entries.len(), 1, "failed commit does not change the live index");
        assert!(s.snapshot(&first).is_ok());
        fs::remove_dir(dir.join("index.json")).unwrap();
        fs::write(dir.join("index.json"), saved).unwrap();
        assert_eq!(Store::load(dir.clone()).index.entries.len(), 1);
        let _ = fs::remove_dir_all(dir);
    }
}
