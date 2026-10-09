"""Pump-and-dump / abnormal-activity risk score (0-100, higher = riskier).

Flags follow the warning signs in SEC/FINRA investor alerts on pump-and-dump schemes: tiny companies and
floats, sudden price and volume spikes without verified news, promotional social activity, dilution and
reverse splits, parabolic runs followed by sharp reversals. Each flag records the measured value. The score
describes suspicious *patterns*; it is never a finding that a stock was manipulated."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..adapters.base import Filing, SocialStats, TickerInfo
from ..adapters.sec import DILUTION_FORMS, GOING_CONCERN_HINT_FORMS

ELEVATED, HIGH = 30, 55
LEVELS = {"low": "Low risk detected", "elevated": "Elevated risk", "high": "High risk — excluded from recommendations"}


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def spike_then_crash(close: pd.Series, lookback: int = 120) -> bool:
    """Rose >= 80% off a 20-day low and later fell >= 40% from that peak within the lookback."""
    c = close.iloc[-lookback:]
    if len(c) < 30:
        return False
    low20 = c.rolling(20, min_periods=5).min()
    run = (c / low20 - 1).to_numpy()
    for i in np.where(run >= 0.8)[0]:
        if (c.iloc[i:] / c.iloc[i] - 1).min() <= -0.4:
            return True
    return False


def assess(daily: pd.DataFrame, info: TickerInfo | None, *, verified_catalyst: bool, promotional_news: int = 0,
           social: SocialStats | None = None, filings: list[Filing] | None = None,
           reverse_splits: list[dict] | None = None, spread_pct: float | None = None,
           negative_major_news: bool = False) -> dict:
    """daily: bars with indicators (latest row = now)."""
    flags: list[dict] = []
    unknown: list[str] = []

    def add(points, key, label, value):
        flags.append({"points": points, "key": key, "label": label, "value": value})

    if daily is None or len(daily) < 25:
        return {"score": None, "level": "unknown", "label": "Not enough history to assess", "flags": [],
                "unknown": ["price history"]}
    c = daily["close"]
    r = daily.iloc[-1]
    price = float(r["close"])
    ret1 = (price / float(c.iloc[-2]) - 1) * 100
    ret5 = (price / float(c.iloc[-6]) - 1) * 100
    ret20 = (price / float(c.iloc[-21]) - 1) * 100
    rv = _f(r.get("rel_vol"))
    max_rv5 = _f(daily["rel_vol"].iloc[-5:].max())
    mcap = info.market_cap if info else None
    flt = (info.shares_float or info.shares_outstanding) if info else None
    float_is_proxy = bool(info and not info.shares_float and info.shares_outstanding)

    if mcap is None:
        unknown.append("market cap")
    elif mcap < 300e6:
        add(20, "micro_cap", "Micro-cap company", f"${mcap / 1e6:,.0f}M market value")
    elif mcap < 1e9:
        add(8, "small_cap", "Small company", f"${mcap / 1e6:,.0f}M market value")
    if price < 5:
        add(18 if price < 1 else 12, "low_price", "Low share price", f"${price:.2f}")
    if ret5 >= 50 or ret20 >= 100:
        add(25, "parabolic", "Parabolic price spike", f"{ret5:+.0f}% in 5 days, {ret20:+.0f}% in 20 days")
    elif ret5 >= 25:
        add(12, "sharp_runup", "Sharp short-term run-up", f"{ret5:+.0f}% in 5 days")
    if max_rv5 is not None and max_rv5 >= 5:
        pts = 20 if max_rv5 >= 10 else 12
        if not verified_catalyst:
            add(pts, "volume_no_news", "Extreme volume without a verified catalyst",
                f"up to {max_rv5:.0f}x normal volume in 5 days, no verified news")
        else:
            add(pts // 3, "volume_spike", "Extreme volume spike (catalyst found)", f"up to {max_rv5:.0f}x normal volume")
    if abs(ret1) >= 15 and not verified_catalyst:
        add(10, "move_no_news", "Large move with no verified company news", f"{ret1:+.0f}% today")
    if flt:
        vol = float(r["volume"])
        tag = " (shares outstanding; free float unavailable)" if float_is_proxy else ""
        if vol / flt >= 0.5:
            add(15, "float_turnover", "Very high float turnover", f"{vol / flt:.1f}x the float traded today{tag}")
        if flt < 20e6:
            add(12, "tiny_float", "Very small float", f"{flt / 1e6:.1f}M shares{tag}")
    else:
        unknown.append("share float")
    dv = _f(r.get("dollar_vol20"))
    if dv is not None and dv < 2e6:
        add(10, "illiquid", "Thin trading", f"${dv / 1e6:.2f}M average daily dollar volume")
    if spread_pct is not None and spread_pct > 1.0:
        add(10, "wide_spread", "Wide bid-ask spread", f"{spread_pct:.2f}%")
    if social is not None and social.mentions_today is not None and social.mentions_avg:
        ratio = social.mentions_today / max(social.mentions_avg, 1)
        if ratio >= 10:
            add(15, "social_spike", "Social-media mention spike", f"{ratio:.0f}x normal mentions ({social.source})")
            if (social.positive_share or 0) >= 0.9:
                add(8, "one_sided_social", "One-sided, promotional-looking social activity",
                    f"{social.positive_share * 100:.0f}% positive mentions")
    elif social is None:
        unknown.append("social-media activity")
    if promotional_news:
        add(min(15, 8 * promotional_news), "promotional", "Promotional content in news flow",
            f"{promotional_news} promotional item(s)")
    if filings is None:
        unknown.append("SEC filings")
    else:
        dil = [f for f in filings if f.form in DILUTION_FORMS]
        if dil:
            add(12, "dilution", "Recent dilution / offering filing",
                ", ".join(sorted({f"{f.form} ({f.filed})" for f in dil}))[:120])
        late = [f for f in filings if f.form in GOING_CONCERN_HINT_FORMS]
        if late:
            add(10, "late_filing", "Late periodic filing (possible going-concern risk)",
                ", ".join(f"{f.form} ({f.filed})" for f in late))
    if reverse_splits:
        add(12, "reverse_split", "Reverse split in the last year",
            ", ".join(f"{s['ratio']} on {s['date']}" for s in reverse_splits))
    elif reverse_splits is None:
        unknown.append("split history")
    if spike_then_crash(c):
        add(12, "spike_crash", "Spike-and-crash pattern", "rose 80%+ then gave back 40%+ within ~6 months")
    elif ret20 >= 40 and ret1 <= -10:
        add(10, "parabolic_reversal", "Sharp reversal after a parabolic run", f"{ret20:+.0f}% in 20d, {ret1:+.0f}% today")
    if negative_major_news:
        add(8, "negative_news", "Major negative announcement", "verified high-impact negative news")

    score = sum(x["points"] for x in flags)
    # Very large, liquid companies are impractical to pump; dampen size-insensitive flags for them.
    if mcap is not None and mcap >= 10e9:
        score = min(score, ELEVATED - 1)
    elif mcap is not None and mcap >= 2e9:
        score = round(score * 0.7)
    score = int(min(100, score))
    level = "high" if score >= HIGH else "elevated" if score >= ELEVATED else "low"
    flags.sort(key=lambda x: -x["points"])
    return {"score": score, "level": level, "label": LEVELS[level], "flags": flags, "unknown": unknown,
            "disclaimer": "Pattern-based estimate of abnormal-activity risk. Not evidence or an accusation of "
                          "manipulation."}
