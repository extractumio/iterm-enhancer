# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Web access settings (AC-52): web.json in the app folder, the user's alone (0600). The
password is kept as a salted PBKDF2-SHA256 hash, never as text. The CLI and the bridge both
write it; the bridge follows its changes."""
import hashlib
import hmac
import json
import secrets
from pathlib import Path

from .. import setup_state
from ..common import APP_DIR

FILE = APP_DIR / "web.json"
SESSIONS = APP_DIR / "web-sessions.json"   # remembered sign-ins (auth.py); deleted when web access goes off
DEFAULTS = {"enabled": False, "port": 8765, "host": "0.0.0.0", "history": 10000, "allow_hosts": [], "password": None}
ITERATIONS = 200_000
MIN_PASSWORD = 8


def load(path: Path = FILE) -> dict:
    """The settings, with defaults for what is not set. ValueError: the file is not valid."""
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name} cannot be read: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return {**DEFAULTS, **raw}


def save(cfg: dict, path: Path = FILE):
    """Atomically, readable by the user only (the CLI and the bridge may write at once)."""
    setup_state.write(path, cfg)


def mtime(path: Path = FILE):
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def hash_password(password: str) -> dict:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"the password must be at least {MIN_PASSWORD} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return {"salt": salt.hex(), "iterations": ITERATIONS, "hash": digest.hex()}


def verifier(stored):
    """password -> bool for a stored hash; nothing matches when there is none."""
    if not isinstance(stored, dict) or not {"salt", "iterations", "hash"} <= stored.keys():
        return lambda password: False
    salt, rounds, want = bytes.fromhex(stored["salt"]), int(stored["iterations"]), bytes.fromhex(stored["hash"])

    def verify(password):
        return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds), want)
    return verify


def update(path: Path = FILE, **changes) -> dict:
    cfg = load(path)
    cfg.update(changes)
    save(cfg, path)
    return cfg
