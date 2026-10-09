"""Market-wide regime: benchmark trends, sector leadership, volatility and breadth."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..adapters.sectors import INDEX_PROXIES, SECTOR_ETFS

GUIDANCE = {
    "bullish": ("Trend-following pullbacks to rising averages, breakout retests and VWAP reclaims have the wind at "
                "their back. Still require confirmation and respect stops."),
    "mixed": ("Be selective: favour relative-strength leaders in leading sectors, demand higher setup scores and "
              "R:R, take partial profits sooner, and consider smaller size."),
    "bearish": ("Long pullback setups fail more often in a falling market. Most dips are not buyable; the scanner "
                "raises the bar for long setups. Cash or no trade is a valid position."),
}


def _pct(a, b):
    return (a / b - 1) * 100 if b else None


def benchmark_summary(df: pd.DataFrame) -> dict | None:
    if df is None or len(df) < 30:
        return None
    f = ind.add_daily_indicators(df)
    r = f.iloc[-1]
    c = f["close"]
    out = {
        "close": float(r["close"]), "as_of": f.index[-1].isoformat(),
        "change_1d": _pct(c.iloc[-1], c.iloc[-2]), "change_5d": _pct(c.iloc[-1], c.iloc[-6]),
        "change_20d": _pct(c.iloc[-1], c.iloc[-21]),
        "above_50": bool(r["close"] > r["sma50"]) if not math.isnan(r["sma50"]) else None,
        "above_200": bool(r["close"] > r["sma200"]) if not math.isnan(r["sma200"]) else None,
        "sma50_rising": (ind.slope(f["sma50"], 10) or 0) > 0 if not math.isnan(r["sma50"]) else None,
        "rsi": float(r["rsi14"]) if not math.isnan(r["rsi14"]) else None,
        "partial_bar": bool(df.attrs.get("partial_last_bar")),
    }
    return out


def realized_vol(df: pd.DataFrame, n=20) -> float | None:
    if df is None or len(df) <= n:
        return None
    r = np.log(df["close"]).diff().iloc[-n:]
    return float(r.std() * np.sqrt(252) * 100)


def breadth(stocks: dict[str, pd.DataFrame]) -> dict:
    """Breadth across the scanned universe (not the whole exchange): labelled as such in the UI."""
    adv = dec = above50 = n50 = 0
    upvol = downvol = 0.0
    for f in stocks.values():
        if f is None or len(f) < 2:
            continue
        c0, c1 = float(f["close"].iloc[-2]), float(f["close"].iloc[-1])
        v = float(f["volume"].iloc[-1])
        if c1 > c0:
            adv += 1
            upvol += v
        elif c1 < c0:
            dec += 1
            downvol += v
        if "sma50" in f and not math.isnan(f["sma50"].iloc[-1]):
            n50 += 1
            above50 += int(c1 > f["sma50"].iloc[-1])
    return {"advancers": adv, "decliners": dec, "ad_ratio": (adv / dec) if dec else None,
            "pct_above_50dma": (above50 / n50 * 100) if n50 else None, "sample_size": n50,
            "up_volume_ratio": (upvol / downvol) if downvol else None,
            "scope": "scanned universe"}


def regime(benchmarks: dict[str, dict | None], br: dict, vol: float | None) -> dict:
    pts, reasons = 0, []
    for sym in ("SPY", "QQQ", "IWM"):
        b = benchmarks.get(sym)
        if not b:
            continue
        for k, label in (("above_50", "above its 50-day"), ("above_200", "above its 200-day"),
                         ("sma50_rising", "50-day rising")):
            if b.get(k) is None:
                continue
            pts += 1 if b[k] else -1
            reasons.append(f"{sym} {'is' if b[k] else 'is not'} {label}" if k != "sma50_rising"
                           else f"{sym} 50-day {'rising' if b[k] else 'falling'}")
    p50 = br.get("pct_above_50dma")
    if p50 is not None and br.get("sample_size", 0) >= 10:
        if p50 >= 60:
            pts += 1
        elif p50 <= 40:
            pts -= 1
        reasons.append(f"{p50:.0f}% of scanned stocks above their 50-day")
    if vol is not None:
        if vol >= 30:
            pts -= 2
            reasons.append(f"High volatility (S&P realized {vol:.0f}%)")
        elif vol >= 20:
            pts -= 1
            reasons.append(f"Elevated volatility (S&P realized {vol:.0f}%)")
        else:
            reasons.append(f"Calm volatility (S&P realized {vol:.0f}%)")
    spy = benchmarks.get("SPY") or {}
    if pts >= 5 and spy.get("above_50"):
        label = "bullish"
    elif pts <= -2 or (spy.get("above_200") is False and spy.get("sma50_rising") is False):
        label = "bearish"
    else:
        label = "mixed"
    return {"regime": label, "score": pts, "reasons": reasons, "guidance": GUIDANCE[label]}


def sector_table(sector_bars: dict[str, pd.DataFrame]) -> list[dict]:
    rows = []
    for name, etf in SECTOR_ETFS.items():
        s = benchmark_summary(sector_bars.get(etf))
        if s:
            rows.append({"sector": name, "etf": etf, **s})
    rows.sort(key=lambda r: -(r.get("change_20d") or -999))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["leadership"] = "leading" if i < 3 else "lagging" if i >= len(rows) - 3 else "neutral"
    return rows


def sector_aligned(sector: str | None, sectors: list[dict]) -> tuple[bool | None, str]:
    row = next((r for r in sectors if r["sector"] == sector), None)
    if not row:
        return None, "sector benchmark unavailable"
    ok = bool(row.get("above_50")) and row["leadership"] != "lagging"
    return ok, f"{sector} ({row['etf']}) ranks #{row['rank']} over 20 days, {'above' if row.get('above_50') else 'below'} its 50-day"


def index_rows(benchmarks: dict[str, dict | None]) -> list[dict]:
    return [{"symbol": k, "name": INDEX_PROXIES[k], **(benchmarks.get(k) or {"unavailable": True})}
            for k in INDEX_PROXIES]
