"""NYSE/Nasdaq regular-session calendar (rule-based holidays, US/Eastern) and data-freshness labels."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
OPEN, CLOSE, EARLY_CLOSE = time(9, 30), time(16, 0), time(13, 0)
PRE_OPEN, AFTER_CLOSE = time(4, 0), time(20, 0)
# One-off closures that no rule produces.
SPECIAL_CLOSURES = {date(2025, 1, 9)}  # National Day of Mourning for President Carter


def _easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def _nth_weekday(y, m, weekday, n):
    d = date(y, m, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(y, m, weekday):
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date | None:
    if d.weekday() == 5:  # Saturday -> Friday, except New Year's Day (NYSE rule 7.2)
        return None if (d.month, d.day) == (1, 1) else d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=32)
def holidays(y: int) -> frozenset[date]:
    days = [
        _observed(date(y, 1, 1)),
        _nth_weekday(y, 1, 0, 3),  # MLK
        _nth_weekday(y, 2, 0, 3),  # Presidents
        _easter(y) - timedelta(days=2),  # Good Friday
        _last_weekday(y, 5, 0),  # Memorial
        _observed(date(y, 6, 19)) if y >= 2022 else None,
        _observed(date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),  # Labor
        _nth_weekday(y, 11, 3, 4),  # Thanksgiving
        _observed(date(y, 12, 25)),
    ]
    # New Year's Day of the following year on a Saturday is not observed on Dec 31 (NYSE rule).
    return frozenset(d for d in days if d) | frozenset(d for d in SPECIAL_CLOSURES if d.year == y)


@lru_cache(maxsize=32)
def early_closes(y: int) -> frozenset[date]:
    out = set()
    thanks = _nth_weekday(y, 11, 3, 4)
    out.add(thanks + timedelta(days=1))
    xmas_eve = date(y, 12, 24)
    if xmas_eve.weekday() < 5 and xmas_eve not in holidays(y):
        out.add(xmas_eve)
    jul3 = date(y, 7, 3)
    if jul3.weekday() < 5 and jul3 not in holidays(y):
        out.add(jul3)
    return frozenset(out)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_trading_day(d: date) -> date:
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days_between(a: date, b: date) -> int:
    """Number of trading days in (a, b]. Negative if b < a."""
    if b < a:
        return -trading_days_between(b, a)
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n


def session_close(d: date) -> time:
    return EARLY_CLOSE if d in early_closes(d.year) else CLOSE


def now_et() -> datetime:
    return datetime.now(ET)


def session_state(t: datetime | None = None) -> str:
    """'pre' | 'open' | 'after' | 'closed' for the given moment (ET)."""
    t = (t or now_et()).astimezone(ET)
    d = t.date()
    if not is_trading_day(d):
        return "closed"
    tt = t.time()
    if OPEN <= tt < session_close(d):
        return "open"
    if PRE_OPEN <= tt < OPEN:
        return "pre"
    if session_close(d) <= tt < AFTER_CLOSE:
        return "after"
    return "closed"


def last_completed_session(t: datetime | None = None) -> date:
    t = (t or now_et()).astimezone(ET)
    d = t.date()
    if is_trading_day(d) and t.time() >= session_close(d):
        return d
    return prev_trading_day(d)


def current_or_last_session(t: datetime | None = None) -> date:
    """The session whose intraday bars are relevant now: today once premarket trading starts, else the last one."""
    t = (t or now_et()).astimezone(ET)
    d = t.date()
    if is_trading_day(d) and t.time() >= PRE_OPEN:
        return d
    return prev_trading_day(d)


@dataclass
class Freshness:
    status: str  # 'real-time' | 'delayed' | 'end-of-day' | 'stale' | 'unavailable' | 'synthetic'
    as_of: str | None
    age_minutes: float | None
    delay_minutes: int
    session: str
    note: str

    def to_dict(self):
        return self.__dict__.copy()


def freshness(as_of: datetime | None, delay_minutes: int, *, synthetic: bool = False,
              now: datetime | None = None, max_age_minutes: float = 30) -> Freshness:
    """Label a data timestamp honestly. Never hides delay or staleness."""
    now = (now or now_et()).astimezone(ET)
    sess = session_state(now)
    if as_of is None:
        return Freshness("unavailable", None, None, delay_minutes, sess, "No timestamped data was received.")
    as_of = as_of.astimezone(ET)
    age = (now - as_of).total_seconds() / 60
    if synthetic:
        return Freshness("synthetic", as_of.isoformat(), round(age, 1), delay_minutes, sess,
                         "DEMO MODE — synthetic data generated for illustration. Not real market prices.")
    if sess != "open":
        last = last_completed_session(now)
        ok = as_of.date() >= last
        note = ("Market closed. Prices are from the last session close; proposed levels may no longer be valid "
                "at the next open." if ok else
                f"Market closed and the newest data ({as_of:%Y-%m-%d %H:%M} ET) predates the last session ({last}).")
        return Freshness("end-of-day" if ok else "stale", as_of.isoformat(), round(age, 1), delay_minutes, sess, note)
    if age > max(max_age_minutes, delay_minutes + 15):
        return Freshness("stale", as_of.isoformat(), round(age, 1), delay_minutes, sess,
                         f"Data is {age:.0f} minutes old. Levels shown may no longer be valid.")
    if delay_minutes > 0:
        return Freshness("delayed", as_of.isoformat(), round(age, 1), delay_minutes, sess,
                         f"Prices are delayed about {delay_minutes} minutes by the data plan.")
    return Freshness("real-time", as_of.isoformat(), round(age, 1), 0, sess, "Real-time feed.")
