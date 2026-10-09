# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Password sign-in, remembered sign-ins, and a brake on guessing: per address, and for all
addresses together (an attacker on IPv6 can change address at will)."""
import hashlib
import ipaddress
import json
import secrets
import threading
import time

from .. import setup_state
from ..common import log

SESSION_DAYS = {True: 30, False: 7}   # over HTTPS, and over plain HTTP (a cookie that can be read in transit)
FREE_ATTEMPTS = 5          # wrong passwords from one address before it has to wait
FREE_ATTEMPTS_ALL = 20     # wrong passwords from all addresses before every address waits
MAX_LOCK = 300             # seconds
COOKIE = "itw"
MAX_SESSIONS = 100         # remembered sign-ins: the ones that expire first go


def client_address(peer, forwarded_for):
    """The browser's address. Behind a proxy on this Mac (`tailscale serve`) every request
    comes from loopback; only then is X-Forwarded-For believed, and only its last entry, which
    that proxy added (earlier ones are whatever the client sent)."""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if loopback and forwarded_for:
        return forwarded_for.split(",")[-1].strip()
    return peer


def lock_for(count, free):
    """Seconds to wait after `count` wrong passwords: none for the first `free`, then 15 s doubling."""
    return min(MAX_LOCK, 2 ** (count - free) * 15) if count >= free else 0


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Auth:
    """Passwords are checked one at a time (site.py); sign-ins also change on the event loop
    (sign-out), so they change and are written under one lock.

    Sign-ins outlive a restart of the bridge in `store` (0600): only each token's SHA-256 and
    expiry, never a token, and only for the password whose `salt` they were made under, so a
    new password signs everyone out; so does switching web access off (access.py)."""

    def __init__(self, verify, store=None, salt=""):
        self._verify = verify        # password -> bool, against the stored hash (config.py)
        self._store, self._salt = store, salt
        self._lock = threading.Lock()
        self._sessions = self._load()   # SHA-256 of a token -> expiry
        self._failures = {}          # address -> (count, locked_until)
        self._all = (0, 0.0)         # all addresses: (count, locked_until)

    def _load(self):
        if not self._store:
            return {}
        try:
            data = json.loads(self._store.read_text())
            if data.get("salt") != self._salt:
                return {}
            now = time.time()
            return {str(k): float(e) for k, e in data["sessions"].items() if float(e) > now}
        except FileNotFoundError:
            return {}
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            log(f"web: remembered sign-ins unreadable, starting without them: {type(e).__name__}: {e}")
            return {}

    def _save(self):
        if not self._store:
            return
        try:
            setup_state.write(self._store, {"salt": self._salt, "sessions": self._sessions})
        except OSError as e:
            log(f"web: remembered sign-ins not saved: {e}")

    def locked_for(self, address):
        until = max(self._failures.get(address, (0, 0.0))[1], self._all[1])
        return max(0, int(until - time.monotonic() + 0.999))

    def check_password(self, password, address, secure=False):
        """A new session token, or None on a wrong password."""
        if self._verify(password or ""):
            self._failures.pop(address, None)
            self._all = (0, 0.0)
            token = secrets.token_urlsafe(32)
            now = time.time()
            with self._lock:
                kept = sorted(((e, t) for t, e in self._sessions.items() if e > now), reverse=True)[:MAX_SESSIONS - 1]
                self._sessions = {t: e for e, t in kept}                      # drop expired sign-ins
                self._sessions[digest(token)] = now + SESSION_DAYS[secure] * 86400
                self._save()
            return token
        now = time.monotonic()
        if len(self._failures) > 1000:            # addresses come and go: keep the ones still waiting
            self._failures = {a: f for a, f in self._failures.items() if f[1] > now}
        count = self._failures.get(address, (0, 0.0))[0] + 1
        self._failures[address] = (count, now + lock_for(count, FREE_ATTEMPTS))
        total = self._all[0] + 1
        self._all = (total, now + lock_for(total, FREE_ATTEMPTS_ALL))
        return None

    def check_session(self, token):
        if not token:
            return False
        expiry = self._sessions.get(digest(token))
        return bool(expiry and expiry > time.time())

    def close(self):
        """This server stops: a password check still running writes no sign-in after it."""
        with self._lock:
            self._store = None

    def sign_out(self, token):
        with self._lock:
            if self._sessions.pop(digest(token or ""), None) is not None:
                self._save()

    @staticmethod
    def cookie_header(token, secure):
        """The sign-in, as a cookie the page, its WebSocket and its Files frames all send.
        HttpOnly: no script reads it; Strict: no other site's page sends it."""
        age = SESSION_DAYS[secure] * 86400 if token else 0
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}" + ("; Secure" if secure else "")
