# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-07: the bridge and the installer talk to fbd only through its private socket. A
program that holds fbd's TCP port (here a fake "squatter" that records every request) gets
neither the bridge secret, nor the token, nor a way to send terminal commands."""
import http.server
import importlib
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bridge"))
sys.path.insert(0, str(REPO / "scripts"))

from fbbridge import backend  # noqa: E402


def recorder(seen, answer):
    class Handler(http.server.BaseHTTPRequestHandler):
        def handle_one(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((self.command, self.path, dict(self.headers), self.rfile.read(n)))
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        do_GET = do_POST = handle_one

        def log_message(self, *a):
            pass
    return Handler


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def get_request(self):  # BaseHTTPRequestHandler wants a client address
        conn, _ = super().get_request()
        return conn, ("local", 0)


class BridgeSocketTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fbs-", dir="/tmp"))  # short: socket paths end at 104 bytes
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        self.squatted = []
        squatter = http.server.ThreadingHTTPServer(("127.0.0.1", 0), recorder(self.squatted, {"build": "evil", "bridge_connected": True}))
        self.port = squatter.server_address[1]
        self.serve(squatter)
        (self.dir / "token").write_text("t" * 32)
        p = mock.patch.dict(os.environ, {"FB_APP_DIR": str(self.dir), "FB_PORT": str(self.port)})
        p.start()
        self.addCleanup(p.stop)
        self.patch = mock.patch.object(backend, "SOCKET", self.dir / "fbd.sock")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def serve(self, server):
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

    def fbd(self):
        """The real fbd's side: the private socket, recording what the bridge sends."""
        got = []
        self.serve(UnixServer(str(self.dir / "fbd.sock"), recorder(got, {"build": "b1", "bridge_connected": True})))
        return got

    def test_without_fbd_the_bridge_sends_nothing_to_the_port(self):
        b = backend.Backend()
        b.proc = mock.Mock(poll=lambda: None)
        self.assertFalse(b.wait_ready(seconds=0.5), "a program on the port is not fbd")
        self.assertFalse(b.answers())
        self.assertEqual(self.squatted, [], "no secret, no state, no command stream request")

    def test_the_bridge_talks_to_fbd_on_the_socket(self):
        got = self.fbd()
        b = backend.Backend()
        self.assertTrue(b.wait_ready(seconds=2))
        self.assertTrue(b.answers())
        method, path, headers, body = got[0]
        self.assertEqual((method, path, headers["X-FB-Bridge"]), ("POST", "/internal/state", b.secret))
        self.assertEqual(self.squatted, [])

    def test_the_installer_asks_health_on_the_socket(self):
        import install_launch
        live = importlib.reload(install_launch)
        self.assertIsNone(live.health(), "nothing on the socket: not healthy, and nothing sent to the port")
        self.assertEqual(self.squatted, [])
        self.fbd()
        self.assertEqual(live.health()["build"], "b1")
        self.assertEqual(self.squatted, [], "the token never went over TCP")



FBD = REPO / "fbd/target/debug/fbd"


@unittest.skipUnless(FBD.is_file(), "fbd not built (cargo build)")
class FbdPortTest(unittest.TestCase):
    """The real fbd: a port another program held means a new token (AC-07)."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="fbp-", dir="/tmp"))
        self.addCleanup(lambda: shutil.rmtree(self.dir, ignore_errors=True))
        (self.dir / "token").write_text("old-token-0123456789abcdef")

    def run_fbd(self, port):
        env = dict(os.environ, FB_APP_DIR=str(self.dir), FB_PORT=str(port), FB_LOG="warn")
        env.pop("FB_NEW_TOKEN", None)
        p = subprocess.Popen([str(FBD)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(lambda: (p.kill(), p.wait()))
        return p

    def wait_for(self, cond, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_after_a_squatter_the_token_is_new(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        p = self.run_fbd(port)
        time.sleep(0.6)
        s.close()  # the squatter leaves while fbd still waits for its port
        self.assertTrue(self.wait_for(lambda: (self.dir / "fbd.sock").exists()), "fbd came up")
        self.assertNotEqual((self.dir / "token").read_text(), "old-token-0123456789abcdef")
        self.assertIsNone(p.poll())

    def test_a_second_fbd_leaves_the_token_alone(self):
        first = self.run_fbd(self.free_port())
        self.assertTrue(self.wait_for(lambda: (self.dir / "fbd.sock").exists()))
        token = (self.dir / "token").read_text()
        port = int(self.free_port())
        holder = socket.socket()
        holder.bind(("127.0.0.1", port))
        holder.listen()
        self.addCleanup(holder.close)
        second = self.run_fbd(port)  # same folder, a busy port: our first fbd answers on the socket
        self.assertEqual(second.wait(10), 2)
        self.assertIn(b"another fbd of this install", second.stderr.read())
        self.assertEqual((self.dir / "token").read_text(), token, "the running fbd's token is kept")
        self.assertIsNone(first.poll())

    @staticmethod
    def free_port():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


if __name__ == "__main__":
    unittest.main()
