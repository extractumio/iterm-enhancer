// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Synthetic Unix peers test timeouts and cancellation without SSH or live state.

use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[test]
fn unicode_events_survive_every_http_frame_boundary() {
    let line = "data: {\"dirs\":[\"/café/日本\"],\"files\":[],\"moved\":[]}\n";
    for i in 0..line.len() {
        let mut buf = line.as_bytes()[..i].to_vec();
        assert!(take_line(&mut buf).is_none());
        buf.extend_from_slice(&line.as_bytes()[i..]);
        let decoded = take_line(&mut buf).unwrap();
        assert_eq!(decoded, line);
        let event = fs_change("devbox.example", decoded.strip_prefix("data:").unwrap()).unwrap();
        assert!(event.data().contains("/café/日本"));
        assert!(buf.is_empty());
    }
}

async fn peer(tag: &str, headers: Option<&'static [u8]>) -> (Agent, tokio::task::JoinHandle<()>) {
    let socket = std::env::temp_dir().join(format!("fbd-peer-{tag}-{}.sock", std::process::id()));
    let _ = std::fs::remove_file(&socket);
    let listener = tokio::net::UnixListener::bind(&socket).unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut bytes = [0; 4096];
        stream.read(&mut bytes).await.unwrap();
        if let Some(headers) = headers { stream.write_all(headers).await.unwrap(); }
        // The client must close its owned connection after a timeout or cancellation.
        let read = tokio::time::timeout(Duration::from_secs(1), stream.read(&mut bytes)).await.unwrap();
        assert!(matches!(read, Ok(0) | Err(_)));
    });
    (Agent { socket, token: "synthetic-token".into(), generation: 1 }, task)
}

fn request() -> hyper::Request<Body> {
    hyper::Request::builder().uri("/api/file").header(header::HOST, crate::agent::HOST).body(Body::empty()).unwrap()
}

#[tokio::test]
async fn a_stalled_response_header_times_out_and_closes_the_connection() {
    let (agent, peer) = peer("headers", None).await;
    let result = send_with_deadline(&agent, request(), Duration::from_millis(30)).await;
    assert!(matches!(result, Err(e) if e.contains("timed out")));
    peer.await.unwrap();
    std::fs::remove_file(agent.socket).unwrap();
}

#[tokio::test]
async fn a_stalled_finite_body_times_out_and_closes_the_connection() {
    let (agent, peer) = peer("body", Some(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nx")).await;
    let (response, connection) = send_with_deadline(&agent, request(), Duration::from_secs(1)).await.unwrap();
    let body = finite_body(response.into_body(), connection, Duration::from_millis(30));
    assert!(body.collect().await.is_err());
    peer.await.unwrap();
    std::fs::remove_file(agent.socket).unwrap();
}

#[tokio::test]
async fn a_trickling_finite_body_has_a_total_deadline() {
    let socket = std::env::temp_dir().join(format!("fbd-peer-trickle-{}.sock", std::process::id()));
    let _ = std::fs::remove_file(&socket);
    let listener = tokio::net::UnixListener::bind(&socket).unwrap();
    let peer = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = [0; 4096];
        stream.read(&mut request).await.unwrap();
        stream.write_all(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n").await.unwrap();
        for _ in 0..20 {
            if stream.write_all(b"1\r\nx\r\n").await.is_err() { return }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("a trickle must not reset the finite body deadline");
    });
    let agent = Agent { socket, token: "synthetic-token".into(), generation: 1 };
    let (response, connection) = send_with_deadline(&agent, request(), Duration::from_secs(1)).await.unwrap();
    assert!(finite_body(response.into_body(), connection, Duration::from_millis(35)).collect().await.is_err());
    peer.await.unwrap();
    std::fs::remove_file(agent.socket).unwrap();
}

#[tokio::test]
async fn long_lived_streams_outlive_the_header_deadline_and_close_on_drop() {
    let (agent, peer) = peer("stream", Some(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")).await;
    let (response, connection) = send_with_deadline(&agent, request(), Duration::from_millis(30)).await.unwrap();
    tokio::time::sleep(Duration::from_millis(60)).await;
    assert!(!peer.is_finished(), "the SSE connection has no total request deadline");
    drop(response);
    drop(connection);
    peer.await.unwrap();
    std::fs::remove_file(agent.socket).unwrap();
}
