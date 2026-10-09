"""Earnings / event-risk filter and event-driven-decline detection."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from ..adapters.base import Event
from ..config import BASE_DIR, ScanSettings
from ..market_calendar import trading_days_between

FOMC_FILE = BASE_DIR / "pullback_radar" / "data" / "fomc_schedule.json"


def load_fomc(path: Path = FOMC_FILE) -> list[Event]:
    """FOMC decision days from a user-maintained file (source: federalreserve.gov). Verify at the source."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for d in data.get("decision_days", []):
        try:
            out.append(Event(None, "fomc", date.fromisoformat(d), "FOMC rate decision and statement (2:00 pm ET)",
                             data.get("source", "federalreserve.gov"), "14:00", "high", data.get("url")))
        except ValueError:
            continue
    return out


def event_risk(symbol: str, style: str, today: date, events: list[Event], s: ScanSettings,
               earnings_known: bool) -> dict:
    """Upcoming events that matter for this trade and whether they trigger the blackout."""
    blackout = s.earnings_blackout_days_swing if style == "swing" else s.earnings_blackout_days_intraday
    upcoming, blocking = [], []
    for e in events:
        if e.symbol not in (symbol, None) or e.date < today:
            continue
        n = trading_days_between(today, e.date)
        item = {**e.to_dict(), "trading_days_away": n}
        horizon = s.swing_hold_days_max if style == "swing" else 1
        if n <= max(horizon, blackout) + 5:
            upcoming.append(item)
        if e.kind == "earnings" and e.symbol == symbol and s.exclude_before_earnings:
            # Same-day after-close earnings count as inside the window for intraday too (overnight gap risk is
            # avoided intraday, but the session is often erratic).
            if n <= blackout:
                blocking.append(f"Earnings in {n} trading day(s) ({e.date}{' ' + e.time_of_day if e.time_of_day else ''})"
                                f" — inside the {blackout}-day blackout")
        if e.kind in ("fomc", "economic") and e.importance == "high" and s.exclude_before_major_macro and n <= (
                1 if style == "intraday" else 0):
            blocking.append(f"{e.description} on {e.date} — inside the macro-event blackout")
    notes = []
    if not earnings_known:
        notes.append("Earnings calendar unavailable — verify the next report date before trading.")
    return {"upcoming": sorted(upcoming, key=lambda x: x["date"]), "blocking": blocking, "notes": notes,
            "next_earnings": next((u for u in sorted(upcoming, key=lambda x: x["date"])
                                   if u["kind"] == "earnings" and u["symbol"] == symbol), None)}


def event_driven_decline(daily: pd.DataFrame, events: list[Event], news: list[dict], symbol: str,
                         lookback: int = 15) -> dict | None:
    """Was the recent drop caused by an event (earnings, guidance, offering, legal) rather than ordinary
    profit-taking? Large gap-downs on news are not treated as normal buy-the-dip opportunities."""
    if daily is None or len(daily) < lookback + 2:
        return None
    recent = daily.iloc[-lookback:]
    prev_close = daily["close"].shift(1).iloc[-lookback:]
    atr = daily["atr14"].iloc[-lookback - 1:-1].to_numpy()
    worst = None
    for i, (ts, row) in enumerate(recent.iterrows()):
        pc = prev_close.iloc[i]
        if pd.isna(pc) or pd.isna(atr[i]):
            continue
        chg = (row["close"] / pc - 1) * 100
        gap = (row["open"] / pc - 1) * 100
        big = chg <= -2 * atr[i] / pc * 100 or gap <= -5
        if big and (worst is None or chg < worst["change_pct"]):
            worst = {"date": ts.date().isoformat(), "change_pct": round(chg, 2), "gap_pct": round(gap, 2)}
    if not worst:
        return None
    d = date.fromisoformat(worst["date"])
    causes = [f"{e.kind} on {e.date}" for e in events
              if e.symbol == symbol and e.kind == "earnings" and abs((e.date - d).days) <= 1]
    causes += [f"{n['category'].replace('_', ' ')} news: {n['headline'][:80]}" for n in news
               if n["sentiment"] == "negative" and n["impact"] == "high"
               and abs((date.fromisoformat(n['published_at'][:10]) - d).days) <= 1]
    if not causes:
        return None
    return {**worst, "causes": causes,
            "message": (f"Event-driven decline on {worst['date']} ({worst['change_pct']:+.1f}%) after "
                        f"{causes[0]}. Not treated as an ordinary pullback: review the revised outlook and let a "
                        "new base form before considering it.")}
