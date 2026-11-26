"""
LAN access security for LOY Studio.

- `LanAuthStore`: password (PBKDF2-SHA256) + per-device session tokens with idle expiry
  and revocation. There is NO default password: phones are locked out until the Mac
  owner sets one.
- `LoginRateLimiter`: per-IP exponential lockout after repeated wrong passwords.
- `host_allowed` / `same_origin`: DNS-rebinding and cross-site request (CSRF) guards.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from app.storage import atomic_write_json, read_json

log = logging.getLogger("loy.security")

PBKDF2_ITERATIONS = 240_000
MIN_PASSWORD_LEN = 6
SESSION_IDLE_TTL_S = 30 * 86400
_LAST_SEEN_WRITE_INTERVAL_S = 600


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()


def _legacy_sha256(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _token_key(token: str) -> str:
    """Sessions are stored by hash so a leaked auth file does not leak live tokens."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LanAuthStore:
    """File-backed LAN password + session store (cached by file mtime)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: Optional[Dict[str, Any]] = None
        self._mtime: Optional[float] = None

    # ---------------- persistence ----------------
    def _empty(self) -> Dict[str, Any]:
        return {"version": 2, "algo": "", "salt": "", "hash": "", "iterations": 0, "sessions": {}}

    def _migrate(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        if raw.get("version") == 2:
            raw.setdefault("sessions", {})
            return raw
        data = self._empty()
        # Legacy v1 file: {"enabled", "salt", "hash"(sha256), "session_secret", "is_default"}.
        # The old default password ("1234") and "auth disabled" both migrate to "no password
        # set", which locks LAN access until the owner chooses a real password on the Mac.
        if raw.get("enabled", True) and not raw.get("is_default", False) and raw.get("hash") and raw.get("salt"):
            data.update(algo="sha256_legacy", salt=str(raw["salt"]), hash=str(raw["hash"]))
        log.info("Migrated LAN auth file to v2 (password_set=%s); phones must sign in again", bool(data["hash"]))
        return data

    def _load(self) -> Dict[str, Any]:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if self._data is not None and mtime == self._mtime:
            return self._data
        raw = read_json(self.path, self._empty, validate=lambda d: isinstance(d, dict))
        data = self._migrate(raw)
        if data is not raw:
            self._save(data)
        else:
            self._data, self._mtime = data, mtime
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        atomic_write_json(self.path, data, private=True)
        self._data = data
        try:
            self._mtime = self.path.stat().st_mtime
        except OSError:
            self._mtime = None

    # ---------------- password ----------------
    @property
    def password_set(self) -> bool:
        return bool(self._load().get("hash"))

    def verify_password(self, password: str) -> bool:
        data = self._load()
        stored = str(data.get("hash") or "")
        if not stored:
            return False
        salt = str(data.get("salt") or "")
        if data.get("algo") == "sha256_legacy":
            ok = hmac.compare_digest(_legacy_sha256(password, salt), stored)
            if ok:  # transparently upgrade to PBKDF2 on first successful login
                self._store_password(data, password)
                self._save(data)
            return ok
        iterations = int(data.get("iterations") or PBKDF2_ITERATIONS)
        return hmac.compare_digest(_pbkdf2(password, salt, iterations), stored)

    def _store_password(self, data: Dict[str, Any], password: str) -> None:
        salt = secrets.token_hex(16)
        data.update(algo="pbkdf2_sha256", salt=salt, iterations=PBKDF2_ITERATIONS,
                    hash=_pbkdf2(password, salt, PBKDF2_ITERATIONS))

    def set_password(self, password: str) -> None:
        """Set a new password (revokes every existing phone session). Empty = lock LAN access."""
        data = self._load()
        if password:
            if len(password) < MIN_PASSWORD_LEN:
                raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
            self._store_password(data, password)
        else:
            data.update(algo="", salt="", hash="", iterations=0)
        data["sessions"] = {}
        self._save(data)

    # ---------------- sessions ----------------
    def create_session(self, ip: str, user_agent: str) -> str:
        data = self._load()
        self._prune(data)
        token = secrets.token_urlsafe(32)
        now = time.time()
        data["sessions"][_token_key(token)] = {
            "id": secrets.token_hex(6),
            "ip": ip,
            "ua": (user_agent or "")[:80],
            "created": round(now),
            "last_seen": round(now),
        }
        self._save(data)
        return token

    def validate_session(self, token: str) -> bool:
        if not token:
            return False
        data = self._load()
        sess = data.get("sessions", {}).get(_token_key(token))
        if not sess:
            return False
        now = time.time()
        if now - float(sess.get("last_seen", 0)) > SESSION_IDLE_TTL_S:
            data["sessions"].pop(_token_key(token), None)
            self._save(data)
            return False
        if now - float(sess.get("last_seen", 0)) > _LAST_SEEN_WRITE_INTERVAL_S:
            sess["last_seen"] = round(now)
            self._save(data)
        return True

    def revoke_session(self, token: str) -> None:
        data = self._load()
        if data["sessions"].pop(_token_key(token), None) is not None:
            self._save(data)

    def revoke_session_id(self, session_id: str) -> bool:
        data = self._load()
        for key, sess in list(data["sessions"].items()):
            if sess.get("id") == session_id:
                data["sessions"].pop(key)
                self._save(data)
                return True
        return False

    def list_sessions(self) -> List[Dict[str, Any]]:
        data = self._load()
        return sorted(
            ({k: v for k, v in s.items()} for s in data.get("sessions", {}).values()),
            key=lambda s: s.get("last_seen", 0),
            reverse=True,
        )

    def _prune(self, data: Dict[str, Any]) -> None:
        now = time.time()
        data["sessions"] = {
            k: v for k, v in data.get("sessions", {}).items()
            if now - float(v.get("last_seen", 0)) <= SESSION_IDLE_TTL_S
        }


class LoginRateLimiter:
    """Per-IP lockout: 5 free attempts, then 30 s doubling up to 15 min."""

    FREE_ATTEMPTS = 5
    BASE_LOCK_S = 30.0
    MAX_LOCK_S = 900.0

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, float]] = {}

    def retry_after(self, ip: str) -> float:
        st = self._state.get(ip)
        if not st:
            return 0.0
        return max(0.0, st.get("locked_until", 0.0) - time.monotonic())

    def record_failure(self, ip: str) -> float:
        st = self._state.setdefault(ip, {"failures": 0.0, "locked_until": 0.0})
        st["failures"] += 1
        extra = int(st["failures"]) - self.FREE_ATTEMPTS
        if extra >= 0:
            lock = min(self.MAX_LOCK_S, self.BASE_LOCK_S * (2 ** extra))
            st["locked_until"] = time.monotonic() + lock
            log.warning("LAN login locked for %s for %.0fs after %d failures", ip, lock, int(st["failures"]))
            return lock
        return 0.0

    def record_success(self, ip: str) -> None:
        self._state.pop(ip, None)


# ---------------- request guards ----------------

def _split_host(host_header: str) -> str:
    host = (host_header or "").strip().lower()
    if host.startswith("["):  # [::1]:18420
        return host[1:host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:
        return host.split(":", 1)[0]
    return host


def host_allowed(host_header: str) -> bool:
    """
    DNS-rebinding guard: only accept Host headers that cannot be an attacker-controlled
    public DNS name: IP literals, localhost, *.localhost and mDNS *.local names.
    """
    host = _split_host(host_header)
    if not host:
        return False
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def same_origin(origin: str, host_header: str) -> bool:
    """CSRF guard: a browser-sent Origin must match the Host it is talking to."""
    if not origin or origin == "null":
        return False
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    return parts.netloc.lower() == (host_header or "").strip().lower()
