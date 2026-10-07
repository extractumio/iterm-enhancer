# SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
"""Password sign-in, remembered sign-ins, and a brake on guessing: per address, and for all
addresses together (an attacker on IPv6 can change address at will)."""
import ipaddress
import secrets
import time

SESSION_DAYS = {True: 30, False: 7}   # over HTTPS, and over plain HTTP (a cookie that can be read in transit)
FREE_ATTEMPTS = 5          # wrong passwords from one address before it has to wait
FREE_ATTEMPTS_ALL = 20     # wrong passwords from all addresses before every address waits
MAX_LOCK = 300             # seconds
COOKIE = "itw"


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


class Auth:
    """Used from one thread at a time (site.py checks passwords one by one)."""

    def __init__(self, verify):
        self._verify = verify        # password -> bool, against the stored hash (config.py)
        self._sessions = {}          # token -> expiry (memory only: a restart signs everyone out)
        self._failures = {}          # address -> (count, locked_until)
        self._all = (0, 0.0)         # all addresses: (count, locked_until)

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
            self._sessions = {t: e for t, e in self._sessions.items() if e > now}   # drop expired sign-ins
            self._sessions[token] = now + SESSION_DAYS[secure] * 86400
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
        expiry = self._sessions.get(token or "")
        if expiry and expiry > time.time():
            return True
        self._sessions.pop(token or "", None)
        return False

    def sign_out(self, token):
        self._sessions.pop(token or "", None)

    @staticmethod
    def cookie_header(token, secure):
        """The sign-in, as a cookie the page, its WebSocket and its Files frames all send.
        HttpOnly: no script reads it; Strict: no other site's page sends it."""
        age = SESSION_DAYS[secure] * 86400 if token else 0
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}" + ("; Secure" if secure else "")
