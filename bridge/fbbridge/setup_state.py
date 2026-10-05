# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Private install-scoped preference requests and results; no iTerm2 dependency."""
import json
import os
import time
import uuid

REQUEST = "iterm-settings-request.json"
RESULT = "iterm-settings-result.json"


def read(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("x") as f:
            os.chmod(tmp, 0o600)
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def request(directory):
    """An explicit install creates a new request; ordinary starts never call this."""
    value = {"id": uuid.uuid4().hex}
    write(directory / REQUEST, value)
    return value["id"]


def wait_result(directory, request_id, seconds=30):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = read(directory / RESULT)
        if value.get("id") == request_id and value.get("status") == "done":
            return value
        time.sleep(0.1)
    return None


def notice(result):
    changes = result.get("changes", [])
    errors = result.get("errors", [])
    if not changes and not errors:
        return None
    message = "iTerm2 settings updated: " + "; ".join(changes) + "." if changes else ""
    if "session restoration" in changes:
        message += " Restart iTerm2 to enable session restoration."
    if any(c.startswith("automatic shell integration") for c in changes):
        message += " Shell integration applies to new supported shell sessions."
    if errors:
        message += " iTerm2 setup incomplete: " + "; ".join(errors) + ". Run the installer again to retry."
    return {"id": result["id"], "message": message.strip(), "error": bool(errors)}
