// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Who is really fbd (AC-07). Another program, another user's included, can hold the TCP
//! port while fbd is not running; it must get neither the bridge's secret nor the panel's
//! token. So the bridge (and the installer) talk to fbd only through a Unix socket in the
//! private app folder, and the panel asks fbd to prove it knows the token before sending it.

use std::path::{Path, PathBuf};

use axum::extract::{Query, Request, State};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::json;
use sha2::{Digest, Sha256};

use crate::http::{err, ApiResult};
use crate::Shared;

/// Unix socket paths end at 104 bytes on macOS, the terminating NUL included.
const SUN_PATH_MAX: usize = 103;

/// The bridge's socket in the app folder: refuses to start if another fbd answers on it
/// (a second fbd with the same folder would take the live bridge's place); a socket left
/// by an fbd that is gone is replaced.
pub fn bind(dir: &Path) -> tokio::net::UnixListener {
    let path = socket_path(dir);
    let fail = |msg: String| -> ! {
        eprintln!("error: {msg}");
        std::process::exit(2);
    };
    if path.as_os_str().len() > SUN_PATH_MAX {
        fail(format!("{} is too long for a socket (set FB_APP_DIR to a shorter folder)", path.display()));
    }
    if std::os::unix::net::UnixStream::connect(&path).is_ok() {
        fail(format!("another fbd uses {}", dir.display()));
    }
    let _ = std::fs::remove_file(&path);
    tokio::net::UnixListener::bind(&path).unwrap_or_else(|e| fail(format!("{}: {e}", path.display())))
}

fn socket_path(dir: &Path) -> PathBuf {
    dir.join("fbd.sock")
}

/// fbd's TCP port, and whether another program than an fbd of this install held it
/// meanwhile: then a panel may have sent it the token, and the caller makes a new one. A
/// previous fbd still shutting down answers on the socket and is waited for (3 s). A port
/// that stays taken exits with code 2; the token is dropped first unless that is our fbd.
pub async fn bind_port(dir: &Path, port: u16) -> Option<(tokio::net::TcpListener, bool)> {
    let mut foreign = false;
    for attempt in 0.. {
        match tokio::net::TcpListener::bind(("127.0.0.1", port)).await {
            Ok(l) => return Some((l, foreign)),
            Err(e) => {
                let ours = std::os::unix::net::UnixStream::connect(socket_path(dir)).is_ok();
                foreign |= !ours;
                if attempt == 15 {
                    if ours {
                        eprintln!("error: port {port}: another fbd of this install runs ({e})");
                    } else {
                        crate::auth::forget_token(dir);
                        eprintln!("error: port {port} is in use by another program ({e}); the token is replaced at the next start");
                    }
                    std::process::exit(2);
                }
                tokio::time::sleep(std::time::Duration::from_millis(200)).await;
            }
        }
    }
    None
}

/// On the socket: `/internal/*` still needs the bridge secret (an fbd of an older build may
/// own a socket a moment longer); `/health` is for the installer and needs nothing.
pub async fn guard(State(app): State<Shared>, req: Request, next: Next) -> Response {
    let given = req.headers().get("x-fb-bridge").and_then(|v| v.to_str().ok());
    let path = req.uri().path();
    if path.starts_with("/internal/") && !matches!((&app.cfg.bridge_secret, given), (Some(s), Some(g)) if same(s, g)) {
        return err(StatusCode::UNAUTHORIZED, "bad_bridge_secret", "Bridge secret required");
    }
    next.run(req).await
}

/// Equal strings, compared in a time that does not depend on where they differ.
pub fn same(a: &str, b: &str) -> bool {
    a.len() == b.len() && a.bytes().zip(b.bytes()).fold(0u8, |d, (x, y)| d | (x ^ y)) == 0
}

pub fn hmac_sha256(key: &[u8], msg: &[u8]) -> [u8; 32] {
    let mut k = [0u8; 64];
    if key.len() > 64 {
        k[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let mut inner = Sha256::new();
    inner.update(k.map(|b| b ^ 0x36));
    inner.update(msg);
    let mut outer = Sha256::new();
    outer.update(k.map(|b| b ^ 0x5c));
    outer.update(inner.finalize());
    outer.finalize().into()
}

/// What the panel expects for nonce `n`: only an fbd that knows the token can say it.
pub fn proof(token: &str, nonce: &str) -> String {
    hmac_sha256(token.as_bytes(), format!("fbd-hello-v1:{nonce}").as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

#[derive(Deserialize)]
pub struct Hello {
    n: String,
}

/// `GET /api/hello?n=<nonce>` (no token): the proof for the panel's nonce. The fixed prefix
/// keeps it from signing anything else.
pub async fn hello(State(app): State<Shared>, Query(q): Query<Hello>) -> ApiResult {
    if !(16..=128).contains(&q.n.len()) || !q.n.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(err(StatusCode::BAD_REQUEST, "bad_nonce", "n must be 16 to 128 hex digits"));
    }
    Ok(Json(json!({"proof": proof(&app.cfg.token, &q.n)})).into_response())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hmac_matches_rfc_4231() {
        let hex = |b: [u8; 32]| b.iter().map(|x| format!("{x:02x}")).collect::<String>();
        assert_eq!(hex(hmac_sha256(b"Jefe", b"what do ya want for nothing?")), "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
        assert_eq!(hex(hmac_sha256(&[0xaa; 131], b"Test Using Larger Than Block-Size Key - Hash Key First")),
                   "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
        // the vector the panel's unit test checks too (ui/test/sha256.test.mjs)
        assert_eq!(proof("0123456789abcdef0123456789abcdef", "00112233445566778899aabbccddeeff"), "30910a59a018ef3078942956ada86eb396a5f08e543e9612ec01ea3568b1a7e0");
    }

    #[test]
    fn same_compares_whole_strings() {
        assert!(same("abc", "abc"));
        assert!(!same("abc", "abd") && !same("abc", "ab") && !same("", "a"));
    }

    /// Holds a TCP port for `ms`, as another program (or a previous fbd) would.
    fn hold_port(ms: u64) -> u16 {
        let l = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(ms));
            drop(l);
        });
        port
    }

    fn fresh_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("fbd-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[tokio::test]
    async fn a_port_another_program_held_means_a_new_token() {
        let dir = fresh_dir("foreign");
        let (_l, foreign) = bind_port(&dir, hold_port(400)).await.unwrap();
        assert!(foreign, "a panel may have sent that program the token");
        let dir2 = fresh_dir("free");
        let free = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = free.local_addr().unwrap().port();
        drop(free);
        assert!(!bind_port(&dir2, port).await.unwrap().1, "a free port keeps the token");
        std::fs::remove_dir_all(&dir).ok();
        std::fs::remove_dir_all(&dir2).ok();
    }

    #[tokio::test]
    async fn our_previous_fbd_on_the_port_keeps_the_token() {
        let dir = fresh_dir("ours");
        let _previous = std::os::unix::net::UnixListener::bind(socket_path(&dir)).unwrap(); // still answers
        let (_l, foreign) = bind_port(&dir, hold_port(400)).await.unwrap();
        assert!(!foreign);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[tokio::test]
    async fn a_stale_socket_is_replaced() {
        let dir = std::env::temp_dir().join(format!("fbd-sock-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // a socket file nobody accepts on, as an fbd that is gone leaves it (a datagram socket:
        // a closed listener could live on in a shell another test forks meanwhile)
        drop(std::os::unix::net::UnixDatagram::bind(socket_path(&dir)).unwrap());
        let l = bind(&dir);
        assert!(std::os::unix::net::UnixStream::connect(socket_path(&dir)).is_ok());
        drop(l);
        std::fs::remove_dir_all(&dir).ok();
    }
}
