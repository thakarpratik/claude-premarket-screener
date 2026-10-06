"""Trade plan for a stock: ways to trade a likely big move WITHOUT predicting its direction.

1. Opening range: wait for the 9:30-10:00 ET range, then follow the break with a stop on the other side.
2. Position size: same dollar risk per trade (the page multiplies the user's risk by the stop distance).
3. Skip flags: same bet as a higher-ranked pick, too little information, evidence split.
4. Options check: is the move the options market prices bigger or smaller than the stock's recent moves?
None of these has been validated on our data yet; the page labels them as untested."""
import math
import time
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

_cache = {}


def opening_ranges(symbols, now):
    """{symbol: range dict} from today's 5-minute bars. Cached for 4 minutes."""
    key = tuple(sorted(symbols))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 240:
        return hit[1]
    out = {s: {"state": "pre_open"} for s in symbols}
    minutes = now.hour * 60 + now.minute
    if now.weekday() >= 5 or not (570 <= minutes < 960) or not symbols:  # outside the session: next range not formed yet
        _cache[key] = (time.time(), out)
        return out
    try:
        d = yf.download(list(symbols) + (["SPY"] if len(symbols) == 1 else []), period="1d", interval="5m",
                        group_by="ticker", auto_adjust=False, progress=False, threads=True)
    except Exception:
        d = None
    for s in symbols:
        try:
            b = d[s].dropna(subset=["Close"])
        except Exception:
            continue
        idx = b.index.tz_convert("America/New_York") if b.index.tz is not None else b.index.tz_localize("America/New_York")
        b = b[idx.date == now.date()]
        idx = idx[idx.date == now.date()]
        if not len(b):
            continue
        first = b[(idx.hour * 60 + idx.minute) < 600]  # 9:30-10:00 bars
        if minutes < 600 or not len(first):
            out[s] = {"state": "forming", "high": float(first.High.max()) if len(first) else None,
                      "low": float(first.Low.min()) if len(first) else None, "last": float(b.Close.iloc[-1])}
            continue
        hi, lo = float(first.High.max()), float(first.Low.min())
        after = b[(idx.hour * 60 + idx.minute) >= 600]
        a_idx = idx[(idx.hour * 60 + idx.minute) >= 600]
        up_t = next((t for t, h in zip(a_idx, after.High) if h > hi), None)
        dn_t = next((t for t, l in zip(a_idx, after.Low) if l < lo), None)
        if up_t is not None and dn_t is not None:
            status, since = "both", max(up_t, dn_t)
        elif up_t is not None:
            status, since = "up", up_t
        elif dn_t is not None:
            status, since = "down", dn_t
        else:
            status, since = "inside", None
        out[s] = {"state": "set", "high": hi, "low": lo, "last": float(b.Close.iloc[-1]), "status": status,
                  "since": since.strftime("%H:%M") if since is not None else None}
    _cache[key] = (time.time(), out)
    return out


def options_check(card, implied, today):
    """Compare the options-implied move with what this stock's recent volatility would suggest."""
    if not implied or not card.get("price"):
        return None
    try:
        days = max(int(np.busday_count(today.isoformat(), implied["expiry"])), 1)
    except Exception:
        return None
    rv = card.get("rv20") or card.get("atr_pct")
    if not rv:
        return None
    expected = 0.8 * rv * math.sqrt(days)  # typical absolute move over `days` sessions
    ratio = implied["pct"] / expected if expected else None
    if ratio is None:
        return None
    label = ("expensive: options price a bigger move than the stock's recent behaviour" if ratio > 1.15 else
             "cheap: options price a smaller move than recent behaviour (worth a look; untested)" if ratio < 0.85 else
             "fairly priced versus recent behaviour")
    return {"expiry": implied["expiry"], "days": days, "implied_pct": implied["pct"], "expected_pct": expected,
            "ratio": ratio, "verdict": "expensive" if ratio > 1.15 else "cheap" if ratio < 0.85 else "fair", "label": label}


SAME_BET_CORR = 0.5


def skip_flags(card, higher_ranked, corr):
    """corr: {symbol: 60-day daily-return correlation with this card's stock}."""
    flags = []
    for other in higher_ranked:
        c = corr.get(other["symbol"])
        if c is not None and c >= SAME_BET_CORR:
            flags.append(f"Same bet as #{other['rank']} {other['symbol']}: their daily moves are {c:.0%} correlated "
                         f"over 60 days. Trade one, not both.")
            break
    if not card.get("analysts") and not card.get("has_options"):
        flags.append("Little information: no analyst coverage and no options market.")
    return flags


def build(card, rng, implied, today, higher_ranked=(), corr=None):
    price = card.get("price") or 0
    atr_d = price * (card.get("atr_pct") or 0) / 100
    skips = skip_flags(card, higher_ranked, corr or {})
    notes = []
    if abs(card.get("bull", 0) - card.get("bear", 0)) <= 1:
        notes.append(f"Evidence split: {card.get('bull', 0)} positive vs {card.get('bear', 0)} negative signals.")
    ne = card.get("next_earnings")
    if ne:
        d = (pd.Timestamp(ne).date() - today).days
        if 0 <= d <= 1:
            notes.append("Earnings due: expect a gap at the open; the range plan starts after it.")
    rng = rng or {"state": "pre_open"}
    if rng.get("state") == "set":
        width = rng["high"] - rng["low"]
        st = rng.get("status")
        verdict = {"up": "long", "down": "short", "both": "stand_aside", "inside": "wait"}[st]
        stop_dist = width
    else:
        verdict, stop_dist = "wait", atr_d
    if skips:
        verdict = "skip"
    return {"verdict": verdict, "range": rng, "atr_dollars": atr_d, "stop_dist": stop_dist,
            "skip": skips, "notes": notes, "options": options_check(card, implied, today)}
