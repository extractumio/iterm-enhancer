// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Regression cases for conditional saves and exclusive rename.

use super::*;
use std::sync::{Arc, Barrier};

fn setup(tag: &str) -> (Roots, PathBuf) {
    let dir = std::env::temp_dir().join(format!("fbd-regression-{tag}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    (Roots::parse(&dir.display().to_string()), dir)
}

#[test]
fn competing_saves_conflict_even_through_symlink_aliases() {
    let (roots, dir) = setup("save");
    let roots = Arc::new(roots);
    let file = dir.join("file.txt");
    fs::write(&file, "original").unwrap();
    let alias = dir.join("alias.txt");
    std::os::unix::fs::symlink(&file, &alias).unwrap();
    let original = etag(&fs::metadata(&file).unwrap());
    let start = Arc::new(Barrier::new(8));
    let threads: Vec<_> = (0..8).map(|n| {
        let (roots, start, original) = (roots.clone(), start.clone(), original.clone());
        let path = if n % 2 == 0 { file.clone() } else { alias.clone() };
        std::thread::spawn(move || {
            let text = format!("writer {n}\n").repeat(16_384);
            start.wait();
            (save(&roots, &path, &text, &original), text)
        })
    }).collect();
    let results: Vec<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    let winners: Vec<_> = results.iter().filter(|(r, _)| r.is_ok()).collect();
    assert_eq!(winners.len(), 1);
    assert_eq!(fs::read_to_string(&file).unwrap(), winners[0].1);
    assert_eq!(etag(&fs::metadata(&file).unwrap()), *winners[0].0.as_ref().unwrap());
    assert!(results.iter().filter(|(r, _)| r.is_err()).all(|(r, _)| matches!(r, Err(OpError::Conflict { .. }))));
    assert_eq!(fs::read_dir(&dir).unwrap().count(), 2);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn maximum_length_names_save_without_long_temporary_names() {
    let (roots, dir) = setup("long");
    for name in ["x".repeat(255), format!("{}x", "é".repeat(127))] {
        let file = touch(&roots, &dir, &name).unwrap();
        save(&roots, &file, "saved", "*").unwrap();
        assert_eq!(fs::read_to_string(file).unwrap(), "saved");
    }
    assert_eq!(fs::read_dir(&dir).unwrap().count(), 2);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn a_save_racing_rename_never_recreates_the_old_path() {
    let (roots, dir) = setup("save-rename");
    let roots = Arc::new(roots);
    let file = dir.join("old.txt");
    let updated = "new content\n".repeat(1 << 20);
    for _ in 0..8 {
        fs::write(&file, "original").unwrap();
        let original = etag(&fs::metadata(&file).unwrap());
        let (start, copy, r, path) = (Arc::new(Barrier::new(2)), updated.clone(), roots.clone(), file.clone());
        let other = start.clone();
        let saving = std::thread::spawn(move || { other.wait(); save(&r, &path, &copy, &original) });
        start.wait();
        let target = rename(&roots, &file, "new.txt").unwrap();
        let saved = saving.join().unwrap();
        assert!(!file.exists(), "Save must not resurrect the renamed path");
        let contents = fs::read_to_string(&target).unwrap();
        if saved.is_ok() { assert_eq!(contents, updated); }
        else { assert!(matches!(saved, Err(OpError::Conflict { .. }))); assert_eq!(contents, "original"); }
        fs::remove_file(target).unwrap();
    }
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn exclusive_rename_preserves_a_target_created_after_preflight() {
    let (_, dir) = setup("rename");
    let (from, to) = (dir.join("from"), dir.join("to"));
    fs::write(&from, "source").unwrap();
    assert!(!to.exists()); // the endpoint's preflight
    fs::write(&to, "other writer").unwrap();
    assert_eq!(rename_exclusive(&from, &to).unwrap_err().kind(), std::io::ErrorKind::AlreadyExists);
    assert_eq!(fs::read_to_string(&from).unwrap(), "source");
    assert_eq!(fs::read_to_string(&to).unwrap(), "other writer");
    fs::remove_file(&to).unwrap();
    fs::hard_link(&from, &to).unwrap();
    assert_eq!(rename_exclusive(&from, &to).unwrap_err().kind(), std::io::ErrorKind::AlreadyExists);
    fs::remove_file(&to).unwrap();
    fs::create_dir(&to).unwrap();
    assert_eq!(rename_exclusive(&from, &to).unwrap_err().kind(), std::io::ErrorKind::AlreadyExists);
    fs::remove_dir(&to).unwrap();
    std::os::unix::fs::symlink(&from, &to).unwrap();
    assert_eq!(rename_exclusive(&from, &to).unwrap_err().kind(), std::io::ErrorKind::AlreadyExists);
    assert!(to.symlink_metadata().unwrap().file_type().is_symlink());
    fs::remove_dir_all(dir).unwrap();
}
