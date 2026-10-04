// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Directory listings: read once on a blocking thread (d_type only, no stat per entry),
//! sort Finder-style, keep in a size-bounded cache, serve pages. Only the rows of a page
//! are stat()ed.

use std::cmp::Ordering;
use std::collections::HashMap;
use std::fs;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering as AO};
use std::sync::Arc;
use std::time::{Duration, Instant};

use parking_lot::Mutex;
use serde::Serialize;
use tokio::sync::watch;

pub const PAGE_MAX: usize = 500;
const READ_WAIT: Duration = Duration::from_secs(15);

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Dir,
    File,
    LinkFile,
    LinkDir,
}

impl Kind {
    fn code(self) -> &'static str {
        match self {
            Kind::Dir => "d",
            Kind::File => "f",
            Kind::LinkFile => "l",
            Kind::LinkDir => "L",
        }
    }
    fn is_dir(self) -> bool {
        matches!(self, Kind::Dir | Kind::LinkDir)
    }
}

pub struct Entry {
    pub name: Box<str>,
    pub kind: Kind,
}

pub struct Listing {
    entries: Vec<Entry>,
    dir_mtime: i128,
    generation: u64,
    read_at: Instant,
    bytes: usize,
    /// last filtered view, so paging through a filtered 500K folder does not re-filter
    filtered: Mutex<Option<(String, bool, Arc<Vec<u32>>)>>,
}

type ReadResult = Result<Arc<Listing>, String>;

enum Slot {
    Loading(watch::Receiver<Option<ReadResult>>, Arc<AtomicBool>),
    Done(ReadResult, Instant),
}

const ERROR_TTL: Duration = Duration::from_secs(1);

pub struct Cache {
    slots: Mutex<HashMap<PathBuf, (Slot, Instant)>>,
    generation: AtomicU64,
    max_bytes: usize,
}

#[derive(Serialize)]
pub struct Row {
    n: String,
    k: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    s: Option<u64>,
}

#[derive(Serialize)]
pub struct Page {
    path: String,
    status: &'static str,
    total: usize,
    offset: usize,
    /// set by the handler: may the panel create or rename entries here
    pub writable: bool,
    #[serde(rename = "gen")]
    generation: u64,
    entries: Vec<Row>,
    /// index of the `locate` name in this (filtered) view, if asked and found
    #[serde(skip_serializing_if = "Option::is_none")]
    located: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

pub fn mtime_ns(m: &fs::Metadata) -> i128 {
    m.mtime() as i128 * 1_000_000_000 + m.mtime_nsec() as i128
}

/// Finder-like order: case-insensitive, runs of digits compared as numbers.
pub fn natural_cmp(a: &str, b: &str) -> Ordering {
    let (ab, bb) = (a.as_bytes(), b.as_bytes());
    let (mut i, mut j) = (0, 0);
    while i < ab.len() && j < bb.len() {
        if ab[i].is_ascii_digit() && bb[j].is_ascii_digit() {
            let (si, sj) = (i, j);
            while i < ab.len() && ab[i].is_ascii_digit() {
                i += 1;
            }
            while j < bb.len() && bb[j].is_ascii_digit() {
                j += 1;
            }
            let na = a[si..i].trim_start_matches('0');
            let nb = b[sj..j].trim_start_matches('0');
            let ord = na.len().cmp(&nb.len()).then_with(|| na.cmp(nb));
            if ord != Ordering::Equal {
                return ord;
            }
        } else {
            // compare one char, case-insensitively
            let ca = a[i..].chars().next().unwrap();
            let cb = b[j..].chars().next().unwrap();
            let ord = if ca.is_ascii() && cb.is_ascii() {
                ca.to_ascii_lowercase().cmp(&cb.to_ascii_lowercase())
            } else {
                ca.to_lowercase().cmp(cb.to_lowercase())
            };
            if ord != Ordering::Equal {
                return ord;
            }
            i += ca.len_utf8();
            j += cb.len_utf8();
        }
    }
    (ab.len() - i).cmp(&(bb.len() - j)).then_with(|| a.cmp(b))
}

fn read_dir_sorted(path: &Path, generation: u64) -> Result<Listing, String> {
    let meta = fs::metadata(path).map_err(|e| e.to_string())?;
    if !meta.is_dir() {
        return Err("Not a directory".into());
    }
    let mut entries = Vec::new();
    let mut bytes = 0;
    for de in fs::read_dir(path).map_err(|e| e.to_string())? {
        let Ok(de) = de else { continue };
        let Ok(ft) = de.file_type() else { continue };
        let kind = if ft.is_dir() {
            Kind::Dir
        } else if ft.is_symlink() {
            match fs::metadata(de.path()) {
                Ok(m) if m.is_dir() => Kind::LinkDir,
                _ => Kind::LinkFile,
            }
        } else {
            Kind::File
        };
        let name = entry_name(de.file_name())?;
        bytes += name.len() + std::mem::size_of::<Entry>();
        entries.push(Entry { name, kind });
    }
    entries.sort_unstable_by(|a, b| b.kind.is_dir().cmp(&a.kind.is_dir()).then_with(|| natural_cmp(&a.name, &b.name)));
    Ok(Listing { entries, dir_mtime: mtime_ns(&meta), generation, read_at: Instant::now(), bytes, filtered: Mutex::new(None) })
}

fn entry_name(name: std::ffi::OsString) -> Result<Box<str>, String> {
    name.into_string().map(String::into_boxed_str)
        .map_err(|_| "Folder contains a name that is not UTF-8; cannot address it safely".into())
}

impl Cache {
    pub fn new(max_bytes: usize) -> Arc<Self> {
        Arc::new(Cache { slots: Mutex::new(HashMap::new()), generation: AtomicU64::new(1), max_bytes })
    }

    /// Folder changed: forget it, so the next request waits for a fresh read. (Serving the
    /// old rows meanwhile would race the fs-change event the panel reacts to.)
    pub fn invalidate(self: &Arc<Self>, path: &Path) {
        let mut slots = self.slots.lock();
        match slots.get(path) {
            Some((Slot::Loading(_, dirty), _)) => dirty.store(true, AO::Relaxed),
            Some((Slot::Done(..), _)) => { slots.remove(path); }
            None => {}
        }
    }

    fn bytes(slots: &HashMap<PathBuf, (Slot, Instant)>) -> usize {
        slots.iter().map(|(p, (s, _))| Self::slot_bytes(p, s)).sum()
    }

    fn slot_bytes(path: &Path, slot: &Slot) -> usize {
        path.as_os_str().len() + std::mem::size_of::<(PathBuf, Slot, Instant)>() + match slot {
            Slot::Done(Ok(l), _) => l.bytes + std::mem::size_of::<Listing>(),
            Slot::Done(Err(e), _) => e.len(),
            Slot::Loading(..) => 0,
        }
    }

    pub fn stats(&self) -> (usize, usize) {
        let slots = self.slots.lock();
        (Self::bytes(&slots), slots.len())
    }

    fn start_read(self: &Arc<Self>, path: PathBuf) -> watch::Receiver<Option<ReadResult>> {
        let (tx, rx) = watch::channel(None);
        let dirty = Arc::new(AtomicBool::new(false));
        {
            let mut slots = self.slots.lock();
            if let Some((Slot::Loading(rx, _), _)) = slots.get(&path) { return rx.clone() }
            slots.insert(path.clone(), (Slot::Loading(rx.clone(), dirty.clone()), Instant::now()));
        }
        let me = self.clone();
        tokio::task::spawn_blocking(move || loop {
            let t0 = Instant::now();
            let generation = me.generation.fetch_add(1, AO::Relaxed);
            // a panic must not leave the slot "loading" forever
            let res: ReadResult = std::panic::catch_unwind(|| read_dir_sorted(&path, generation))
                .unwrap_or_else(|_| Err("internal error while reading the folder".into()))
                .map(Arc::new);
            match &res {
                Ok(l) => tracing::info!(event = "ls.done", path = %path.display(), entries = l.entries.len(), ms = t0.elapsed().as_millis() as u64),
                Err(e) => tracing::info!(event = "ls.error", path = %path.display(), error = %e),
            }
            if !me.finish_read(&path, &dirty, &tx, res) { continue }
            me.evict();
            break;
        });
        rx
    }

    /// Invalidation and publication share the slot lock: every waiter gets the reread.
    fn finish_read(&self, path: &Path, dirty: &AtomicBool, tx: &watch::Sender<Option<ReadResult>>, res: ReadResult) -> bool {
        let mut slots = self.slots.lock();
        if dirty.swap(false, AO::Relaxed) { return false }
        slots.insert(path.to_path_buf(), (Slot::Done(res.clone(), Instant::now()), Instant::now()));
        let _ = tx.send(Some(res));
        true
    }

    fn evict(&self) {
        let mut slots = self.slots.lock();
        let mut total = Self::bytes(&slots);
        if total <= self.max_bytes {
            return;
        }
        let mut by_age: Vec<(PathBuf, Instant, usize)> = slots
            .iter()
            .filter_map(|(p, (s, t))| if let Slot::Done(..) = s { Some((p.clone(), *t, Self::slot_bytes(p, s))) } else { None })
            .collect();
        by_age.sort_by_key(|x| x.1);
        for (p, _, n) in by_age {
            if total <= self.max_bytes {
                break;
            }
            slots.remove(&p);
            total -= n;
        }
    }

    async fn get(self: &Arc<Self>, path: &Path) -> Option<ReadResult> {
        let hit = {
            let mut slots = self.slots.lock();
            match slots.get_mut(path) {
                Some((slot, used)) => {
                    *used = Instant::now();
                    match slot {
                        Slot::Done(r, at) => Some(Ok((r.clone(), *at))),
                        Slot::Loading(rx, _) => Some(Err(rx.clone())),
                    }
                }
                None => None,
            }
        };
        let rx = match hit {
            Some(Ok((r, at))) => {
                // errors are retried after a second (a folder may come back after a checkout);
                // listings are re-validated by the folder's mtime (FSEvents also invalidates)
                let stale = match &r {
                    Err(_) => at.elapsed() > ERROR_TTL,
                    Ok(l) => {
                        l.read_at.elapsed() > Duration::from_millis(200)
                            && !tokio::fs::metadata(path).await.map(|m| mtime_ns(&m) == l.dir_mtime).unwrap_or(false)
                    }
                };
                if !stale {
                    return Some(r);
                }
                self.start_read(path.to_path_buf())
            }
            Some(Err(rx)) => rx,
            None => self.start_read(path.to_path_buf()),
        };
        let mut rx = rx;
        match tokio::time::timeout(READ_WAIT, rx.wait_for(|v| v.is_some())).await {
            Ok(Ok(v)) => v.clone(),
            _ => None, // still reading
        }
    }

    pub async fn page(self: &Arc<Self>, path: &Path, offset: usize, limit: usize, filter: &str, hidden: bool, locate: Option<&str>) -> Page {
        let limit = limit.clamp(1, PAGE_MAX);
        let p = path.to_string_lossy().to_string();
        let empty = |status, error| Page { path: p.clone(), status, total: 0, offset, writable: false, generation: 0, entries: vec![], error, located: None };
        let listing = match self.get(path).await {
            None => return empty("loading", None),
            Some(Err(e)) => return empty("error", Some(e)),
            Some(Ok(l)) => l,
        };
        // filtering and per-row stat() can block on slow volumes: keep them off the async workers
        let (path, filter, locate) = (path.to_path_buf(), filter.to_string(), locate.map(str::to_string));
        tokio::task::spawn_blocking(move || Self::build_page(&listing, &path, offset, limit, &filter, hidden, locate.as_deref()))
            .await
            .unwrap_or_else(|_| empty("error", Some("internal error".into())))
    }

    fn build_page(listing: &Listing, path: &Path, offset: usize, limit: usize, filter: &str, hidden: bool, locate: Option<&str>) -> Page {
        let p = path.to_string_lossy().to_string();
        let filter_lc = filter.to_lowercase();
        let view: Option<Arc<Vec<u32>>> = if filter_lc.is_empty() && hidden {
            None
        } else {
            let mut cached = listing.filtered.lock();
            match cached.as_ref() {
                Some((f, h, v)) if *f == filter_lc && *h == hidden => Some(v.clone()),
                _ => {
                    let v: Vec<u32> = listing
                        .entries
                        .iter()
                        .enumerate()
                        .filter(|(_, e)| (hidden || !e.name.starts_with('.')) && (filter_lc.is_empty() || e.name.to_lowercase().contains(&filter_lc)))
                        .map(|(i, _)| i as u32)
                        .collect();
                    let v = Arc::new(v);
                    *cached = Some((filter_lc.clone(), hidden, v.clone()));
                    Some(v)
                }
            }
        };
        let total = view.as_ref().map_or(listing.entries.len(), |v| v.len());
        let located = locate.and_then(|name| match &view {
            Some(v) => v.iter().position(|&i| &*listing.entries[i as usize].name == name),
            None => listing.entries.iter().position(|e| &*e.name == name),
        });
        let idx: Vec<usize> = match &view {
            Some(v) => v.iter().skip(offset).take(limit).map(|&i| i as usize).collect(),
            None => (offset..total.min(offset.saturating_add(limit))).collect(),
        };
        let entries = idx
            .into_iter()
            .map(|i| {
                let e = &listing.entries[i];
                let meta = if e.kind.is_dir() { None } else { fs::metadata(path.join(&*e.name)).ok() };
                Row { n: e.name.to_string(), k: e.kind.code(), s: meta.map(|m| m.len()) }
            })
            .collect();
        Page { path: p, status: "ready", total, offset, writable: false, generation: listing.generation, entries, error: None, located }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn invalidation_during_a_scan_keeps_waiters_until_fresh_publication() {
        let c = Cache::new(64 << 20);
        let path = PathBuf::from("/synthetic-folder");
        let (tx, rx) = watch::channel(None);
        let dirty = Arc::new(AtomicBool::new(false));
        c.slots.lock().insert(path.clone(), (Slot::Loading(rx.clone(), dirty.clone()), Instant::now()));
        let mut second = c.start_read(path.clone());
        c.invalidate(&path);
        assert!(!c.finish_read(&path, &dirty, &tx, Err("obsolete scan".into())));
        assert!(rx.borrow().is_none());
        assert!(matches!(c.slots.lock().get(&path), Some((Slot::Loading(..), _))));
        assert_eq!(c.generation.load(AO::Relaxed), 1, "no duplicate worker started");
        assert!(c.finish_read(&path, &dirty, &tx, Err("fresh scan".into())));
        assert_eq!(second.wait_for(|v| v.is_some()).await.unwrap().as_ref().unwrap().as_ref().err().unwrap(), "fresh scan");
        assert_eq!(rx.borrow().as_ref().unwrap().as_ref().err().unwrap(), "fresh scan");
    }

    #[test]
    fn lossy_names_are_refused_instead_of_aliasing_another_file() {
        use std::os::unix::ffi::OsStringExt;
        assert!(entry_name(std::ffi::OsString::from_vec(b"a\xff.txt".to_vec())).is_err());
        assert_eq!(&*entry_name("a\u{fffd}.txt".into()).unwrap(), "a\u{fffd}.txt");
    }

    #[test]
    fn errors_and_empty_folders_are_evicted_with_their_slot_overhead() {
        let c = Cache::new(1);
        c.slots.lock().insert(PathBuf::from("/missing"), (Slot::Done(Err("missing".into()), Instant::now()), Instant::now()));
        let empty = Listing { entries: vec![], dir_mtime: 0, generation: 1, read_at: Instant::now(), bytes: 0, filtered: Mutex::new(None) };
        c.slots.lock().insert(PathBuf::from("/empty"), (Slot::Done(Ok(Arc::new(empty)), Instant::now()), Instant::now()));
        assert!(c.stats().0 > 1);
        c.evict();
        assert_eq!(c.stats(), (0, 0));
    }

    #[test]
    fn natural_order() {
        let mut v = vec!["f10.txt", "F2.txt", "f1.txt", "a", "B", "f02b", "Ärger", "z"];
        v.sort_by(|a, b| natural_cmp(a, b));
        assert_eq!(v, vec!["a", "B", "f1.txt", "F2.txt", "f02b", "f10.txt", "z", "Ärger"]);
    }

    #[tokio::test(flavor = "multi_thread")]
    async fn pages_and_filter() {
        let dir = std::env::temp_dir().join(format!("fbd-test-{}", std::process::id()));
        fs::create_dir_all(dir.join("sub")).unwrap();
        for i in 0..1200 {
            fs::write(dir.join(format!("f{i}.txt")), b"x").unwrap();
        }
        fs::write(dir.join(".hidden"), b"").unwrap();
        let c = Cache::new(64 << 20);
        let p = c.page(&dir, 0, 3, "", true, Some("f10.txt")).await;
        assert_eq!(p.total, 1202);
        assert_eq!(p.entries[0].n, "sub");
        assert_eq!(p.entries[1].n, ".hidden");
        assert_eq!(p.entries[2].n, "f0.txt");
        assert_eq!(p.entries[2].s, Some(1));
        assert_eq!(p.located, Some(2 + 10)); // sub, .hidden, f0..f9, then f10
        let beyond = c.page(&dir, usize::MAX, 500, "", true, None).await;
        assert_eq!(beyond.status, "ready");
        assert!(beyond.entries.is_empty());
        let p = c.page(&dir, 0, 500, "f119", false, None).await;
        assert_eq!(p.total, 11); // f119, f1190..f1199
        let p = c.page(&dir.join("missing"), 0, 10, "", true, None).await;
        assert_eq!(p.status, "error");
        fs::remove_dir_all(&dir).unwrap();
    }
}
