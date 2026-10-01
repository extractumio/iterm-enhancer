// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! File operations: save (etag-checked, atomic, metadata-preserving), create file/folder,
//! rename, move to Trash. All writes are limited to the writable roots.

use std::ffi::CString;
use std::fs;
use std::io::Write;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

use crate::files::{etag, Roots};

#[derive(Debug, PartialEq)]
pub enum OpError {
    /// 400
    BadName(String),
    /// 403
    Denied(String),
    /// 404
    Missing(String),
    /// 409
    Exists(String),
    /// 409, with the current etag
    Conflict { message: String, etag: Option<String> },
    /// 500 / io
    Io(String),
}

impl OpError {
    pub fn code(&self) -> &'static str {
        match self {
            OpError::BadName(_) => "bad_name",
            OpError::Denied(_) => "outside_writable_roots",
            OpError::Missing(_) => "not_found",
            OpError::Exists(_) => "exists",
            OpError::Conflict { .. } => "conflict",
            OpError::Io(_) => "io",
        }
    }
    pub fn message(&self) -> String {
        match self {
            OpError::BadName(m) | OpError::Denied(m) | OpError::Missing(m) | OpError::Exists(m) | OpError::Io(m) => m.clone(),
            OpError::Conflict { message, .. } => message.clone(),
        }
    }
}

/// Map an I/O error to the panel-facing error, naming the file.
pub(crate) fn io(e: std::io::Error, path: &Path) -> OpError {
    match e.kind() {
        std::io::ErrorKind::NotFound => OpError::Missing(format!("{}: No such file or directory", name_of(path))),
        std::io::ErrorKind::PermissionDenied => OpError::Denied(format!("{}: Permission denied", name_of(path))),
        std::io::ErrorKind::AlreadyExists => OpError::Exists(format!("\"{}\" already exists", name_of(path))),
        _ => OpError::Io(format!("{}: {e}", name_of(path))),
    }
}

fn name_of(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| p.display().to_string())
}

/// A single path component: not empty, not `.`/`..`, no `/` or NUL, ≤ 255 bytes.
fn check_component(c: &str) -> Result<(), OpError> {
    if c.is_empty() {
        return Err(OpError::BadName("A name is required".into()));
    }
    if c == "." || c == ".." {
        return Err(OpError::BadName(format!("\"{c}\" is not a valid name")));
    }
    if c.contains('\0') || c.contains('/') {
        return Err(OpError::BadName(format!("\"{c}\" contains a character that is not allowed")));
    }
    if c.len() > 255 {
        return Err(OpError::BadName("Name is too long (max 255 bytes)".into()));
    }
    Ok(())
}

fn writable(allowed: bool) -> Result<(), OpError> {
    if allowed { Ok(()) } else { Err(OpError::Denied("Read-only: outside writable folders".into())) }
}

fn check_writable(roots: &Roots, p: &Path) -> Result<(), OpError> {
    writable(roots.allows(p))
}

/// Rename and trash act on the entry: a symlink is judged by where it lives.
fn check_entry_writable(roots: &Roots, p: &Path) -> Result<(), OpError> {
    writable(roots.allows_entry(p))
}

fn exists_in(name: &str, parent: &Path) -> OpError {
    OpError::Exists(format!("\"{}\" already exists in {}", name, name_of(parent)))
}

/// Save `text` over `path` if its etag still equals `if_match` (or `if_match` is "*").
/// Writes the symlink target, via a temp file in the same folder, copying mode, ACLs
/// and extended attributes from the original, then renames over it.
pub fn save(roots: &Roots, path: &Path, text: &str, if_match: &str) -> Result<String, OpError> {
    let deleted = || OpError::Conflict { message: format!("{} was deleted on disk", name_of(path)), etag: None };
    let (real, meta) = match fs::canonicalize(path) {
        Ok(r) => {
            let m = fs::metadata(&r).map_err(|e| io(e, path))?;
            (r, Some(m))
        }
        Err(_) if path.symlink_metadata().is_ok() => {
            return Err(OpError::Conflict { message: format!("{} is a broken symlink", name_of(path)), etag: None });
        }
        // "*" recreates a file that was deleted on disk (its folder must still exist)
        Err(_) if if_match == "*" => {
            let parent = path.parent().and_then(|p| fs::canonicalize(p).ok()).ok_or_else(deleted)?;
            (parent.join(path.file_name().unwrap_or_default()), None)
        }
        Err(_) => return Err(deleted()),
    };
    check_writable(roots, &real)?;
    if let Some(m) = &meta {
        let current = etag(m);
        if if_match != "*" && if_match != current {
            return Err(OpError::Conflict { message: format!("{} changed on disk", name_of(path)), etag: Some(current) });
        }
    }
    let dir = real.parent().ok_or_else(|| OpError::Io("no parent folder".into()))?;
    // unique per call: two saves in flight must not share a temp file
    static SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    let seq = SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let tmp = dir.join(format!(".{}.fb-{}-{seq}.tmp", name_of(&real), std::process::id()));
    let result = (|| {
        let mut f = fs::OpenOptions::new().write(true).create_new(true).open(&tmp).map_err(|e| io(e, &tmp))?;
        f.write_all(text.as_bytes()).map_err(|e| io(e, path))?;
        f.sync_all().map_err(|e| io(e, path))?;
        drop(f);
        if let Some(m) = &meta {
            copy_metadata(&real, &tmp, m.permissions());
        }
        fs::rename(&tmp, &real).map_err(|e| io(e, path))
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result?;
    Ok(etag(&fs::metadata(&real).map_err(|e| io(e, path))?))
}

fn copy_metadata(from: &Path, to: &Path, perms: fs::Permissions) {
    let (Ok(f), Ok(t)) = (CString::new(from.as_os_str().as_bytes()), CString::new(to.as_os_str().as_bytes())) else { return };
    // SAFETY: valid NUL-terminated paths; a null state is allowed by copyfile(3)
    let rc = unsafe { libc::copyfile(f.as_ptr(), t.as_ptr(), std::ptr::null_mut(), libc::COPYFILE_METADATA) };
    if rc != 0 {
        let _ = fs::set_permissions(to, perms);
    }
}

/// Create a folder `name` (may contain `/` for nested folders) inside `parent`.
pub fn mkdir(roots: &Roots, parent: &Path, name: &str) -> Result<PathBuf, OpError> {
    let name = name.trim_matches('/');
    for c in name.split('/') {
        check_component(c)?;
    }
    let target = parent.join(name);
    check_writable(roots, &target)?;
    if target.symlink_metadata().is_ok() {
        return Err(exists_in(name, parent));
    }
    fs::create_dir_all(&target).map_err(|e| io(e, &target))?;
    Ok(target)
}

/// Create an empty file `name` inside `parent`.
pub fn touch(roots: &Roots, parent: &Path, name: &str) -> Result<PathBuf, OpError> {
    check_component(name)?;
    let target = parent.join(name);
    check_writable(roots, &target)?;
    match fs::OpenOptions::new().write(true).create_new(true).open(&target) {
        Ok(_) => Ok(target),
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => Err(exists_in(name, parent)),
        Err(e) => Err(io(e, &target)),
    }
}

/// Rename `path` to `name` in the same folder. A case-only rename is allowed.
pub fn rename(roots: &Roots, path: &Path, name: &str) -> Result<PathBuf, OpError> {
    check_component(name)?;
    let parent = path.parent().ok_or_else(|| OpError::BadName("Cannot rename /".into()))?;
    let target = parent.join(name);
    check_entry_writable(roots, path)?;
    check_entry_writable(roots, &target)?;
    let src = path.symlink_metadata().map_err(|e| io(e, path))?;
    if let Ok(dst) = target.symlink_metadata() {
        // the same directory entry (case-only rename), not merely the same file via a symlink
        use std::os::unix::fs::MetadataExt;
        let same = src.dev() == dst.dev() && src.ino() == dst.ino();
        if !same {
            return Err(OpError::Exists(format!("\"{}\" already exists", name)));
        }
    }
    fs::rename(path, &target).map_err(|e| io(e, path))?;
    Ok(target)
}

/// Move items to the macOS Trash (NSFileManager, no Finder prompt). Returns per-item errors.
pub fn trash(roots: &Roots, paths: &[PathBuf]) -> Vec<(PathBuf, OpError)> {
    let mut errors = Vec::new();
    for p in paths {
        if let Err(e) = check_entry_writable(roots, p) {
            errors.push((p.clone(), e));
            continue;
        }
        if let Err(e) = p.symlink_metadata() {
            errors.push((p.clone(), io(e, p)));
            continue;
        }
        if let Err(e) = trash::delete(p) {
            errors.push((p.clone(), OpError::Io(format!("{}: {e}", name_of(p)))));
        }
    }
    errors
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn setup(tag: &str) -> (Roots, PathBuf) {
        let d = std::env::temp_dir().join(format!("fbd-ops-{tag}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        (Roots::parse(&std::env::temp_dir().display().to_string()), fs::canonicalize(&d).unwrap())
    }

    #[test]
    fn save_checks_etag_and_keeps_mode_and_symlink() {
        let (roots, d) = setup("save");
        let f = d.join("app.py");
        fs::write(&f, "old").unwrap();
        fs::set_permissions(&f, fs::Permissions::from_mode(0o750)).unwrap();
        let e0 = etag(&fs::metadata(&f).unwrap());
        let e1 = save(&roots, &f, "import os\n", &e0).unwrap();
        assert_eq!(fs::read_to_string(&f).unwrap(), "import os\n");
        assert_eq!(fs::metadata(&f).unwrap().permissions().mode() & 0o777, 0o750);
        // stale etag → conflict, file unchanged
        let err = save(&roots, &f, "mine", &e0).unwrap_err();
        assert!(matches!(&err, OpError::Conflict { etag: Some(e), .. } if *e == e1), "{err:?}");
        assert_eq!(fs::read_to_string(&f).unwrap(), "import os\n");
        // overwrite
        save(&roots, &f, "forced", "*").unwrap();
        assert_eq!(fs::read_to_string(&f).unwrap(), "forced");
        // through a symlink: the target is written, the link stays a link
        let link = d.join("link.py");
        std::os::unix::fs::symlink(&f, &link).unwrap();
        save(&roots, &link, "via link", "*").unwrap();
        assert!(fs::symlink_metadata(&link).unwrap().file_type().is_symlink());
        assert_eq!(fs::read_to_string(&f).unwrap(), "via link");
        // deleted on disk: a normal save conflicts, "*" recreates it
        fs::remove_file(&f).unwrap();
        assert!(matches!(save(&roots, &f, "again", &e1), Err(OpError::Conflict { etag: None, .. })));
        save(&roots, &f, "again", "*").unwrap();
        assert_eq!(fs::read_to_string(&f).unwrap(), "again");
        // outside roots
        assert!(matches!(save(&roots, Path::new("/etc/hosts"), "x", "*"), Err(OpError::Denied(_))));
        // no temp files left behind
        assert_eq!(fs::read_dir(&d).unwrap().count(), 2);
    }

    #[test]
    fn create_rename_validate() {
        let (roots, d) = setup("create");
        let f = touch(&roots, &d, "util.rs").unwrap();
        assert_eq!(fs::metadata(&f).unwrap().len(), 0);
        assert_eq!(touch(&roots, &d, "util.rs").unwrap_err(), OpError::Exists(format!("\"util.rs\" already exists in {}", name_of(&d))));
        assert_eq!(touch(&roots, &d, "").unwrap_err(), OpError::BadName("A name is required".into()));
        assert_eq!(touch(&roots, &d, "..").unwrap_err(), OpError::BadName("\"..\" is not a valid name".into()));
        assert!(matches!(touch(&roots, &d, "a/b"), Err(OpError::BadName(_))));
        let m = mkdir(&roots, &d, "db/migrations").unwrap();
        assert!(m.is_dir());
        assert!(matches!(mkdir(&roots, &d, "db"), Err(OpError::Exists(_))));
        let r = rename(&roots, &f, "lib.rs").unwrap();
        assert!(r.exists() && !f.exists());
        touch(&roots, &d, "other.rs").unwrap();
        assert!(matches!(rename(&roots, &r, "other.rs"), Err(OpError::Exists(_))));
        // case-only rename on a case-insensitive volume
        let c = rename(&roots, &r, "Lib.rs").unwrap();
        assert!(c.exists());
        assert!(matches!(mkdir(&roots, Path::new("/usr/local"), "x"), Err(OpError::Denied(_))));
    }

    #[test]
    fn symlinks_are_judged_by_where_they_live() {
        let (roots, d) = setup("links");
        let out = d.join("to-usr");
        std::os::unix::fs::symlink("/usr/local", &out).unwrap();
        // a link inside the roots can be renamed even though it points outside
        let moved = rename(&roots, &out, "usr-link").unwrap();
        assert!(fs::symlink_metadata(&moved).unwrap().file_type().is_symlink());
        // renaming onto a symlink that points at the source must not replace the link
        let a = touch(&roots, &d, "a.txt").unwrap();
        std::os::unix::fs::symlink(&a, d.join("b.txt")).unwrap();
        assert!(matches!(rename(&roots, &a, "b.txt"), Err(OpError::Exists(_))));
        assert!(fs::symlink_metadata(d.join("b.txt")).unwrap().file_type().is_symlink());
        // a broken symlink is not silently replaced by "*"
        std::os::unix::fs::symlink(d.join("nowhere"), d.join("broken.txt")).unwrap();
        assert!(matches!(save(&roots, &d.join("broken.txt"), "x", "*"), Err(OpError::Conflict { .. })));
        // an entry outside the roots is refused even if it points inside
        assert!(!roots.allows_entry(Path::new("/usr/local/bin/anything")));
    }

    #[test]
    fn trash_reports_missing() {
        let (roots, d) = setup("trash");
        let gone = d.join("gone.txt");
        let errs = trash(&roots, &[gone.clone()]);
        assert_eq!(errs.len(), 1);
        assert_eq!(errs[0].1, OpError::Missing("gone.txt: No such file or directory".into()));
    }
}
