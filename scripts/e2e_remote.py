#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""AC-37 / AC-38 end to end against a real Linux host with sshd: a throwaway Debian
container, its own test key, ssh config, app folder and fbd port (nothing of the user's
is read or changed). Enabling goes through fbbridge.hosts.enable, as the panel's Enable
button does. Needs Docker and the Linux toolchain (make toolchain).

    python3 scripts/e2e_remote.py [--amd64]     (--amd64: an x86_64 host, emulated)
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME, PORT, SSH_PORT, SECRET = "fb-e2e-sshd", 47837, 2223, "e2e-remote-secret-0123456789"
DOCKERFILE = """FROM debian:12-slim
RUN apt-get update -qq && apt-get install -y -qq openssh-server procps >/dev/null && rm -rf /var/lib/apt/lists/* \\
 && useradd -m -s /bin/bash tester && mkdir -p /run/sshd /home/tester/.ssh
COPY key.pub /home/tester/.ssh/authorized_keys
RUN chown -R tester:tester /home/tester/.ssh && chmod 700 /home/tester/.ssh && chmod 600 /home/tester/.ssh/authorized_keys \\
 && printf 'PasswordAuthentication no\\nAllowStreamLocalForwarding yes\\n' >> /etc/ssh/sshd_config
CMD ["/usr/sbin/sshd", "-D", "-e"]
"""
ok = True


def check(name, passed, detail=""):
    global ok
    ok &= bool(passed)
    print(f"{'PASS' if passed else 'FAIL'} {name}{f' ({detail})' if detail else ''}", flush=True)


def sh(*cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def main():
    amd64 = "--amd64" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="fbr-"))  # short: socket paths must stay under 104 bytes
    sh("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "fb-e2e-remote", "-f", str(work / "key"))
    (work / "Dockerfile").write_text(DOCKERFILE)
    plat = ["--platform", "linux/amd64"] if amd64 else []
    sh("docker", "build", "-q", *plat, "-t", NAME, str(work))
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    sh("docker", "run", "-d", *plat, "--name", NAME, "--hostname", "fbvm", "-p", f"127.0.0.1:{SSH_PORT}:22", NAME)
    (work / "config").write_text(f"Host fbtest\n  HostName 127.0.0.1\n  Port {SSH_PORT}\n  User tester\n  IdentityFile {work}/key\n"
                                 f"  IdentitiesOnly yes\n  StrictHostKeyChecking accept-new\n  UserKnownHostsFile {work}/known_hosts\n")
    (work / "ssh").write_text(f'#!/bin/sh\nexec ssh -F "{work}/config" "$@"\n')
    (work / "ssh").chmod(0o755)
    env = dict(os.environ, FB_SSH=str(work / "ssh"), FB_APP_DIR=str(work / "app"), FB_PORT=str(PORT))
    fbd = None
    try:
        for _ in range(120):  # sshd up (a fresh container can take a while)
            if subprocess.run([str(work / "ssh"), "-o", "BatchMode=yes", "fbtest", "true"], capture_output=True).returncode == 0:
                break
            time.sleep(0.25)
        os.environ.update(FB_SSH=env["FB_SSH"], FB_APP_DIR=env["FB_APP_DIR"])
        sys.path.insert(0, str(REPO / "bridge"))
        sys.path.insert(0, str(REPO / "scripts"))
        import agents
        from fbbridge import agentctl, common, hosts, remote
        common.LOG_DIR = work / "logs"  # the bridge's log of this run, never the user's
        aid = agents.agent_id()
        record = hosts.enable(["fbtest"], binary_for=lambda p: agents.build([p])[p], agent_id=aid)
        check("AC-38 Enable prepares the host", record["agent_id"] == aid and record["platform"] == ("linux-x86_64" if amd64 else "linux-aarch64"), record)
        layout = agentctl.ssh(["fbtest"], "cd ~/.iterm-filebrowser && ls bin logs && readlink bin/fbd-agent && stat -c %a .").split()
        check("AC-38 the helper is in ~/.iterm-filebrowser/bin, private", f"fbd-agent-{aid}" in layout and layout[-1] == "700", layout)
        fbd = subprocess.Popen([str(REPO / "fbd/target/debug/fbd")], env=dict(env, FB_BRIDGE_SECRET=SECRET, FB_LOG="warn"), stderr=subprocess.DEVNULL)
        time.sleep(1)
        token = (work / "app/token").read_text().strip()

        def post(path, body):
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "X-FB-Bridge": SECRET})
            urllib.request.urlopen(req, timeout=5).read()

        def api(method, path, body=None, headers=None):
            h = {"X-FB-Token": token, "X-FB-Host": "fbtest", **(headers or {})}
            if body is not None:
                h["Content-Type"] = "application/json"
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=json.dumps(body).encode() if body is not None else None,
                                         method=method, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"null")

        post("/internal/state", {"key": "k", "session": "s", "cwd": "/home/tester", "host": "fbtest", "window": "w", "panel": True, "mode": "remote"})
        remotes = remote.Remotes(post)
        t0 = time.monotonic()
        while remotes.status("fbtest", ["fbtest"], "fbvm")["state"] != "up" and time.monotonic() - t0 < 20:
            time.sleep(0.25)
        check("AC-37 the bridge connects the host's agent through ssh", remotes.status("fbtest", ["fbtest"], "fbvm")["state"] == "up", f"{time.monotonic() - t0:.1f} s")
        events = []

        def listen():
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/events?t={token}")
            with urllib.request.urlopen(req) as resp:
                ev = None
                for raw in resp:
                    line = raw.decode().strip()
                    if line.startswith("event:"):
                        ev = line[6:].strip()
                    elif line.startswith("data:") and ev == "fs-change":
                        events.append(json.loads(line[5:]))
        threading.Thread(target=listen, daemon=True).start()
        status, page = api("GET", "/api/ls?path=/home/tester")
        check("AC-37 a listing of the host", status == 200 and any(e["n"] == ".bashrc" for e in page["entries"]))
        status, made = api("POST", "/api/fs/touch", {"parent": "/home/tester", "name": "hello.txt"})
        check("AC-37 create on the host", status == 201 and made["path"] == "/home/tester/hello.txt")
        status, f = api("GET", "/api/file?path=/home/tester/hello.txt")
        status, saved = api("PUT", "/api/file?path=/home/tester/hello.txt", {"text": "from the Mac\n"}, {"If-Match": f'"{f["etag"]}"'})
        check("AC-37 save on the host", status == 200 and agentctl.ssh(["fbtest"], "cat hello.txt") == "from the Mac\n")
        check("AC-37 a stale save is refused", api("PUT", "/api/file?path=/home/tester/hello.txt", {"text": "x"}, {"If-Match": f'"{f["etag"]}"'})[0] == 409)
        check("AC-37 writes outside the host's roots are refused", api("POST", "/api/fs/touch", {"parent": "/etc", "name": "x"})[0] == 403)
        post("/internal/state", {"key": "k", "session": "s", "cwd": "/home/tester", "host": "fbtest", "window": "w", "panel": True, "mode": "remote", "title": "t"})
        time.sleep(1)
        agentctl.ssh(["fbtest"], "echo hi > made-on-host.txt")
        for _ in range(20):
            if any(e.get("host") == "fbtest" for e in events):
                break
            time.sleep(0.25)
        check("AC-37 a change on the host arrives tagged with the host", any(e.get("host") == "fbtest" and "/home/tester" in e.get("dirs", []) for e in events))
        status, trashed = api("POST", "/api/fs/trash", {"paths": ["/home/tester/hello.txt"]})
        check("AC-37 trash on the host", status == 200 and "hello.txt" in agentctl.ssh(["fbtest"], "ls ~/.local/share/Trash/files"))
        remotes.stop()
        time.sleep(1)
        left = agentctl.ssh(["fbtest"], 'ps -eo comm | grep -c "^fbd-agent" || true').strip()
        check("AC-37 the agent exits with its connection and leaves nothing in /tmp", left == "0" and not agentctl.ssh(["fbtest"], "ls -A /tmp").strip(), f"{left} running")
        log = agentctl.ssh(["fbtest"], "cat ~/.iterm-filebrowser/logs/agent.log")
        check("AC-38 the agent logged to ~/.iterm-filebrowser/logs/agent.log", "event=\"start\"" in log and "mode=\"agent\"" in log, log[-160:])
        hosts.remove(["fbtest"])
        check("AC-38 remove takes it all off the host", agentctl.ssh(["fbtest"], "ls -A ~").split().count(".iterm-filebrowser") == 0 and not agentctl.entry("fbtest"))
    finally:
        if fbd:
            fbd.terminate()
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        subprocess.run(["rm", "-rf", str(work)])
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
