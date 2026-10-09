"""Shared HTTP client for providers: TTL cache, per-provider rate limiting, retries with backoff,
and an error log the status endpoint can show."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

import httpx

from .base import DataUnavailable, NotConfigured

log = logging.getLogger("pullback_radar.http")

PROVIDER_ERRORS: dict[str, deque] = {}
PROVIDER_STATS: dict[str, dict] = {}


def record_error(provider: str, msg: str):
    PROVIDER_ERRORS.setdefault(provider, deque(maxlen=20)).append({"at": time.time(), "error": msg})
    log.warning("%s: %s", provider, msg)


class RateLimiter:
    """Sliding-window limiter: at most `per_minute` calls in any 60-second window."""

    def __init__(self, per_minute: int, sleep=time.sleep, clock=time.monotonic):
        self.per_minute = max(1, per_minute)
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()
        self.sleep, self.clock = sleep, clock

    def acquire(self):
        with self.lock:
            while True:
                now = self.clock()
                while self.calls and now - self.calls[0] >= 60:
                    self.calls.popleft()
                if len(self.calls) < self.per_minute:
                    self.calls.append(now)
                    return
                self.sleep(60 - (now - self.calls[0]) + 0.01)


class TTLCache:
    """In-memory TTL cache with an optional on-disk layer for immutable data (e.g. closed sessions)."""

    def __init__(self, disk_dir: Path | None = None):
        self.mem: dict[str, tuple[float, object]] = {}
        self.disk_dir = disk_dir
        self.lock = threading.Lock()

    def get(self, key: str):
        with self.lock:
            hit = self.mem.get(key)
            if hit and (hit[0] == 0 or hit[0] > time.time()):
                return hit[1]
        if self.disk_dir:
            p = self.disk_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")
            if p.exists():
                try:
                    return json.loads(p.read_text())
                except (OSError, ValueError):
                    return None
        return None

    def set(self, key: str, value, ttl: float | None, persist: bool = False):
        with self.lock:
            self.mem[key] = (0 if ttl is None else time.time() + ttl, value)
        if persist and self.disk_dir:
            self.disk_dir.mkdir(parents=True, exist_ok=True)
            p = self.disk_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")
            try:
                p.write_text(json.dumps(value))
            except (OSError, TypeError):
                pass


class ProviderClient:
    def __init__(self, provider: str, base_url: str, per_minute: int, *, auth_params: dict | None = None,
                 headers: dict | None = None, cache: TTLCache | None = None, transport=None,
                 max_retries: int = 3, sleep=time.sleep, timeout: float = 20.0):
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.auth_params = auth_params or {}
        self.limiter = RateLimiter(per_minute, sleep=sleep)
        self.cache = cache or TTLCache()
        self.max_retries = max_retries
        self.sleep = sleep
        self.client = httpx.Client(timeout=timeout, headers=headers or {}, transport=transport)
        PROVIDER_STATS.setdefault(provider, {"requests": 0, "errors": 0, "cache_hits": 0, "last_ok": None})

    def get_json(self, path: str, params: dict | None = None, *, ttl: float | None = 60, persist=False):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        params = dict(params or {})
        key = f"{self.provider}|{url}|{json.dumps(params, sort_keys=True, default=str)}"
        if ttl != 0:
            hit = self.cache.get(key)
            if hit is not None:
                PROVIDER_STATS[self.provider]["cache_hits"] += 1
                return hit
        stats = PROVIDER_STATS[self.provider]
        last_err = None
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            stats["requests"] += 1
            try:
                r = self.client.get(url, params={**params, **self.auth_params})
            except httpx.HTTPError as e:
                last_err = f"network error: {type(e).__name__}"
                self.sleep(min(2 ** attempt, 16))
                continue
            if r.status_code in (401, 403):
                stats["errors"] += 1
                record_error(self.provider, f"HTTP {r.status_code} for {path} (check API key / plan entitlements)")
                raise NotConfigured(f"{self.provider} rejected the request (HTTP {r.status_code}). "
                                    "Check the API key and that your plan includes this data.")
            if r.status_code == 404:
                raise DataUnavailable(f"{self.provider}: not found ({path})")
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                retry_after = r.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 2 ** attempt
                self.sleep(min(wait, 30))
                continue
            if r.status_code >= 400:
                stats["errors"] += 1
                record_error(self.provider, f"HTTP {r.status_code} for {path}")
                raise DataUnavailable(f"{self.provider}: HTTP {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                raise DataUnavailable(f"{self.provider}: invalid JSON response")
            stats["last_ok"] = time.time()
            if ttl != 0:
                self.cache.set(key, data, ttl, persist=persist)
            return data
        stats["errors"] += 1
        record_error(self.provider, f"giving up on {path}: {last_err}")
        raise DataUnavailable(f"{self.provider}: request failed after retries ({last_err})")
