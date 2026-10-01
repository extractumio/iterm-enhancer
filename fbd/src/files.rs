// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Reading files for the viewer: text with limits, binary detection, raw bytes for images.

use std::fs;
use std::io::Read;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::listing::mtime_ns;
use crate::ops::{io, OpError};

pub const TRUNCATED_BYTES: usize = 1 << 20;
const SNIFF_BYTES: usize = 8192;

#[derive(Serialize, Debug)]
pub struct FileView {
    pub path: String,
    pub size: u64,
    pub etag: String,
    pub binary: bool,
    pub mime: Option<String>,
    pub truncated: bool,
    pub utf8: bool,
    pub writable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
}

pub fn etag(m: &fs::Metadata) -> String {
    format!("{}-{}-{}", mtime_ns(m), m.ino(), m.len())
}

/// Folders where the panel may write, canonicalized (so /tmp matches /private/tmp).
pub struct Roots(Vec<PathBuf>);

impl Roots {
    pub fn parse(spec: &str) -> Self {
        let home = std::env::var("HOME").unwrap_or_default();
        Roots(
            spec.split(':')
                .filter(|s| !s.is_empty())
                .map(|s| s.replacen("$HOME", &home, 1))
                .filter_map(|s| fs::canonicalize(s).ok())
                .collect(),
        )
    }

    /// True if `path` is inside a writable root. The nearest existing ancestor is resolved
    /// through symlinks (so a link cannot escape); parts that do not exist yet are appended.
    pub fn allows(&self, path: &Path) -> bool {
        if path.components().any(|c| matches!(c, std::path::Component::ParentDir)) {
            return false;
        }
        let mut existing = path;
        let mut rest = Vec::new();
        let real = loop {
            match fs::canonicalize(existing) {
                Ok(r) => break r,
                Err(_) => match (existing.parent(), existing.file_name()) {
                    (Some(parent), Some(name)) => {
                        rest.push(name.to_owned());
                        existing = parent;
                    }
                    _ => return false,
                },
            }
        };
        let full = rest.iter().rev().fold(real, |acc, n| acc.join(n));
        self.0.iter().any(|root| full.starts_with(root))
    }

    /// Like `allows`, but for the directory entry itself: a symlink is judged by where the
    /// link lives, not by its target (rename and trash act on the link).
    pub fn allows_entry(&self, path: &Path) -> bool {
        match (path.parent(), path.file_name()) {
            (Some(parent), Some(name)) if name != ".." => {
                fs::canonicalize(parent).is_ok_and(|p| self.0.iter().any(|root| p.join(name).starts_with(root)))
            }
            _ => false,
        }
    }

    pub fn list(&self) -> Vec<String> {
        self.0.iter().map(|p| p.display().to_string()).collect()
    }
}

/// Open `path` for reading only if it is a regular file, judged on the open file: a FIFO or
/// a device (`/dev/zero` linked from a README) would block or never end (AC-03).
pub fn open_regular(path: &Path) -> Result<(fs::File, fs::Metadata), OpError> {
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::OpenOptionsExt;
    let kind = |meta: &fs::Metadata| match () {
        _ if meta.is_dir() => Err(OpError::BadName("Is a directory".into())),
        _ if !meta.is_file() => Err(OpError::BadName("Not a regular file".into())),
        _ => Ok(()),
    };
    // never open a device (opening a serial port has effects), then check again what was
    // opened, non-blocking, in case the path was replaced meanwhile
    kind(&fs::metadata(path).map_err(|e| io(e, path))?)?;
    let f = fs::OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK).open(path).map_err(|e| io(e, path))?;
    let meta = f.metadata().map_err(|e| io(e, path))?;
    kind(&meta)?;
    unsafe { libc::fcntl(f.as_raw_fd(), libc::F_SETFL, libc::fcntl(f.as_raw_fd(), libc::F_GETFL) & !libc::O_NONBLOCK) };
    Ok((f, meta))
}

pub fn read_view(path: &Path, text_max: u64, roots: &Roots) -> Result<FileView, OpError> {
    let (f, meta) = open_regular(path)?;
    let size = meta.len();
    let truncated = size > text_max;
    let want = if truncated { TRUNCATED_BYTES } else { size as usize };
    let mut buf = Vec::with_capacity(want.min(64 << 20));
    f.take(want as u64).read_to_end(&mut buf).map_err(|e| io(e, path))?;
    let binary = buf[..buf.len().min(SNIFF_BYTES)].contains(&0);
    let mime = mime_guess::from_path(path).first().map(|m| m.essence_str().to_string());
    let (text, utf8) = if binary {
        (None, true)
    } else {
        match String::from_utf8(buf) {
            Ok(s) => (Some(s), true),
            Err(e) => {
                // a truncated read may cut a multi-byte char at the end: that is still UTF-8
                let valid = e.utf8_error().valid_up_to();
                let bytes = e.into_bytes();
                if truncated && bytes.len() - valid < 4 {
                    (Some(String::from_utf8_lossy(&bytes[..valid]).into_owned()), true)
                } else {
                    (Some(String::from_utf8_lossy(&bytes).into_owned()), false)
                }
            }
        }
    };
    // owner-writable is a good-enough hint; the real check happens at save time
    let perm_ok = std::os::unix::fs::PermissionsExt::mode(&meta.permissions()) & 0o200 != 0;
    Ok(FileView {
        path: path.display().to_string(),
        size,
        etag: etag(&meta),
        binary,
        mime,
        truncated,
        utf8,
        writable: !binary && !truncated && utf8 && perm_ok && roots.allows(path),
        text,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("fbd-files-{}", std::process::id()));
        fs::create_dir_all(&d).unwrap();
        d.join(name)
    }

    #[test]
    fn text_binary_huge_missing() {
        let roots = Roots::parse(&std::env::temp_dir().display().to_string());
        let t = tmp("a.rs");
        fs::write(&t, "fn main() {}\n").unwrap();
        let v = read_view(&t, 1 << 20, &roots).unwrap();
        assert!(!v.binary && v.utf8 && !v.truncated && v.writable);
        assert_eq!(v.text.as_deref(), Some("fn main() {}\n"));

        let b = tmp("x.ico");
        fs::write(&b, [0u8, 0, 1, 0, 1, 0]).unwrap();
        let v = read_view(&b, 1 << 20, &roots).unwrap();
        assert!(v.binary && v.text.is_none() && !v.writable);

        let h = tmp("big.log");
        fs::write(&h, "é".repeat(TRUNCATED_BYTES)).unwrap(); // 2 MB of 2-byte chars
        let v = read_view(&h, 1 << 20, &roots).unwrap();
        assert!(v.truncated && v.utf8 && !v.writable);
        assert!(v.text.unwrap().len() <= TRUNCATED_BYTES);

        let l = tmp("latin1.txt");
        fs::write(&l, [b'c', b'a', b'f', 0xe9]).unwrap();
        let v = read_view(&l, 1 << 20, &roots).unwrap();
        assert!(!v.utf8 && !v.writable);

        assert!(matches!(read_view(&tmp("missing"), 1 << 20, &roots), Err(OpError::Missing(_))));
        assert_eq!(read_view(t.parent().unwrap(), 1 << 20, &roots).unwrap_err(), OpError::BadName("Is a directory".into()));
    }

    #[test]
    fn only_regular_files_are_read() {
        let roots = Roots::parse("/nonexistent");
        let started = std::time::Instant::now();
        let fifo = tmp("pipe");
        let _ = fs::remove_file(&fifo);
        let c = std::ffi::CString::new(fifo.as_os_str().as_encoded_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
        for p in [Path::new("/dev/zero"), Path::new("/dev/random"), fifo.as_path()] {
            assert_eq!(read_view(p, 1 << 20, &roots).unwrap_err(), OpError::BadName("Not a regular file".into()), "{p:?}");
            assert!(open_regular(p).is_err());
        }
        assert!(started.elapsed() < std::time::Duration::from_secs(1), "nothing blocked or read forever");
    }

    #[test]
    fn roots_block_outside_and_symlink_escape() {
        let roots = Roots::parse(&std::env::temp_dir().display().to_string());
        assert!(!roots.allows(Path::new("/etc/hosts")));
        let link = tmp("escape");
        let _ = fs::remove_file(&link);
        std::os::unix::fs::symlink("/etc", &link).unwrap();
        assert!(!roots.allows(&link.join("hosts")));
        assert!(roots.allows(&tmp("new-file.txt")));
    }
}
