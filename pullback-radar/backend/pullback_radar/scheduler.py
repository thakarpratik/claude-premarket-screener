"""Background scheduler: periodic scans per active user, alert evaluation and paper-trade updates.

Cadence follows the market session: every 5 minutes while the market is open, every 15 minutes in the
pre-market and after-hours sessions, hourly otherwise. One user's failure never stops the loop."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .db import AuthSession, Trade, User, UserSettings, WatchlistItem
from .market_calendar import now_et, session_state

log = logging.getLogger("pullback_radar.scheduler")
CADENCE = {"open": 300, "pre": 900, "after": 900, "closed": 3600}


def active_user_ids(db) -> list[int]:
    """Users with a watchlist, an open paper trade, or a session in the last 7 days."""
    recent = datetime.now(timezone.utc) - timedelta(days=7)
    ids = set(db.scalars(select(WatchlistItem.user_id)))
    ids |= set(db.scalars(select(Trade.user_id).where(Trade.status == "open", Trade.mode == "paper")))
    ids |= set(db.scalars(select(AuthSession.user_id).where(AuthSession.created_at >= recent)))
    return sorted(ids)


def run_once(state) -> int:
    from .app import execute_scan
    from .config import ScanSettings
    n = 0
    with state.Session() as db:
        for uid in active_user_ids(db):
            user = db.get(User, uid)
            if not user:
                continue
            row = db.get(UserSettings, uid)
            try:
                settings = ScanSettings(**(row.data if row else {}))
            except Exception:  # noqa: BLE001
                settings = ScanSettings()
            lock = state.scan_locks.setdefault(uid, threading.Lock())
            if not lock.acquire(blocking=False):
                continue
            try:
                execute_scan(state, db, user, settings, "scheduler")
                db.commit()
                n += 1
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("scheduled scan failed for user %s", uid)
            finally:
                lock.release()
    return n


def scheduler_loop(state):
    if not state.hub.ready:
        log.warning("scheduler idle: data providers not configured")
        return
    state.stop.wait(10)  # let the API come up first
    while not state.stop.is_set():
        t0 = time.time()
        try:
            n = run_once(state)
            log.info("scheduled scan finished for %d user(s) in %.1fs", n, time.time() - t0)
        except Exception:  # noqa: BLE001
            log.exception("scheduler iteration failed")
        sess = session_state(state.hub.market.now() if state.hub.synthetic else now_et())
        state.stop.wait(max(30, CADENCE[sess] - (time.time() - t0)))
