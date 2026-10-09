"""Password hashing (scrypt) and opaque session tokens (only their SHA-256 is stored)."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

_N, _R, _P = 2 ** 14, 8, 1
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
        return hmac.compare_digest(calc.hex(), dk)
    except (ValueError, TypeError):
        return False


def validate_credentials(email: str, pw: str) -> list[str]:
    errs = []
    if not EMAIL_RE.match(email or ""):
        errs.append("Enter a valid email address")
    if len(pw or "") < 10:
        errs.append("Password must be at least 10 characters")
    return errs


def new_token() -> tuple[str, str]:
    tok = secrets.token_urlsafe(32)
    return tok, token_hash(tok)


def token_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def expiry(days: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


class LoginThrottle:
    """At most `limit` failed attempts per key in `window` seconds."""

    def __init__(self, limit=8, window=900):
        self.limit, self.window = limit, window
        self.fails: dict[str, deque] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        q = self.fails[key]
        now = time.time()
        while q and now - q[0] > self.window:
            q.popleft()
        return len(q) >= self.limit

    def fail(self, key: str):
        self.fails[key].append(time.time())

    def reset(self, key: str):
        self.fails.pop(key, None)
