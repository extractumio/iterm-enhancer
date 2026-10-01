# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""HTTP to fbd over its private socket (AC-07): the bridge and the installer never talk to
whatever holds the TCP port, so a program on it gets neither the bridge secret nor a way to
send terminal commands. Standard library only (the installer runs on Python 3.9)."""
import http.client
import socket


class Connection(http.client.HTTPConnection):
    def __init__(self, sock_path, timeout=2.0):
        super().__init__("fbd", timeout=timeout)
        self.sock_path = str(sock_path)

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock_path)
        except OSError:
            s.close()
            raise
        self.sock = s


def request(sock_path, method, path, body=None, headers=None, timeout=2.0):
    """(status, body bytes); OSError when fbd does not answer on the socket."""
    c = Connection(sock_path, timeout)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()
