"""Intraday pullback detector: opening range, VWAP reclaim/support, ORB retests, 1/5/15-minute structure.

Works on one session of 1-minute bars (pre-market included). All logic uses bars up to the evaluated minute."""
from __future__ import annotations

import math
from datetime import time

import numpy as np
import pandas as pd

from .. import indicators as ind
from .common import Level, SetupAnalysis, Signal, TradePlan, fmt

# Approximate cumulative share of a U.S. session's volume by time of day (typical U-shaped profile).
_VOL_CURVE = [(570, 0.0), (600, 0.13), (630, 0.22), (660, 0.29), (720, 0.41), (780, 0.51), (840, 0.61),
              (900, 0.74), (930, 0.83), (960, 1.0)]


def expected_volume_fraction(minute_of_day: int) -> float:
    xs, ys = zip(*_VOL_CURVE)
    return float(np.interp(minute_of_day, xs, ys))


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def premarket_metrics(bars1: pd.DataFrame, prev_close: float | None, avg_daily_vol: float | None) -> dict:
    pre = bars1[bars1.index.time < time(9, 30)]
    if pre.empty or not prev_close:
        return {"premarket_available": False}
    last = float(pre["close"].iloc[-1])
    vol = float(pre["volume"].sum())
    return {
        "premarket_available": True, "premarket_last": last, "premarket_gap_pct": (last / prev_close - 1) * 100,
        "premarket_high": float(pre["high"].max()), "premarket_low": float(pre["low"].min()),
        "premarket_volume": vol,
        "premarket_volume_pct_of_avg": (vol / avg_daily_vol * 100) if avg_daily_vol else None,
    }


def analyze_intraday(bars1: pd.DataFrame, symbol: str, daily: pd.DataFrame, *, or_minutes: int = 15,
                     spy1: pd.DataFrame | None = None, _check_invalidation: bool = True) -> SetupAnalysis:
    """bars1: one session's 1-minute bars up to now (ET index). daily: daily bars+indicators for prior sessions."""
    a = SetupAnalysis(symbol, "intraday", False, "Avoid", "insufficient_data", None, None, None)
    if bars1 is None or bars1.empty or daily is None or len(daily) < 21:
        a.reasons_avoid.append("Intraday or daily history unavailable")
        return a
    day = bars1.index[-1].date()
    prior = daily[daily.index.date < day]
    if prior.empty:
        a.reasons_avoid.append("No prior session for reference")
        return a
    prev_close = float(prior["close"].iloc[-1])
    prev_high = float(prior["high"].iloc[-1])
    avg_vol = _f(prior["volume"].iloc[-20:].mean())
    d_atr = _f(prior["atr14"].iloc[-1]) if "atr14" in prior else None
    a.metrics.update(premarket_metrics(bars1, prev_close, avg_vol))
    a.metrics.update({"prev_close": prev_close, "prev_high": prev_high, "daily_atr": d_atr})
    reg = bars1[(bars1.index.time >= time(9, 30)) & (bars1.index.time < time(16, 0))]
    a.price = float(bars1["close"].iloc[-1])
    a.as_of = (bars1.index[-1] + pd.Timedelta(minutes=1)).isoformat()
    if reg.empty:
        a.technical_state = "premarket"
        a.reasons_avoid.append("Regular session has not opened; setups form after the opening range")
        return a
    elapsed = len(reg)
    if elapsed < or_minutes + 10:
        a.technical_state = "opening_range_forming"
        a.reasons_avoid.append(f"Opening range ({or_minutes} min) still forming — wait for structure")
        return a

    o_rng = reg.iloc[:or_minutes]
    orh, orl = float(o_rng["high"].max()), float(o_rng["low"].min())
    vw = ind.vwap(reg)
    b5 = ind.resample(reg, 5)
    b15 = ind.resample(reg, 15)
    # Use the 5-minute bar of each 1-minute bar's close for VWAP alignment.
    vw5 = pd.Series([float(vw[vw.index < t + pd.Timedelta(minutes=5)].iloc[-1]) for t in b5.index], index=b5.index)
    atr5 = _f(ind.atr(b5, 14).iloc[-1]) or _f(ind.true_range(b5).mean())
    ema9, ema20 = ind.ema(b5["close"], 9), ind.ema(b5["close"], 20)
    c = a.price
    v_now = float(vw.iloc[-1])
    hod, lod = float(reg["high"].max()), float(reg["low"].min())
    opn = float(reg["open"].iloc[0])
    gap = (opn / prev_close - 1) * 100
    mod = bars1.index[-1].hour * 60 + bars1.index[-1].minute + 1
    frac = expected_volume_fraction(min(mod, 960))
    cum_vol = float(reg["volume"].sum())
    rvol = cum_vol / (avg_vol * frac) if avg_vol and frac > 0 else None
    v5_avg = float(b5["volume"].mean())
    last5, prev5 = b5.iloc[-1], b5.iloc[-2] if len(b5) >= 2 else None
    recent = b5.iloc[-6:]
    pb_low = float(recent["low"].min())

    a.metrics.update({
        "opening_range_high": orh, "opening_range_low": orl, "opening_range_minutes": or_minutes, "vwap": v_now,
        "high_of_day": hod, "low_of_day": lod, "gap_pct": gap, "atr_5m": atr5, "relative_volume_est": rvol,
        "cumulative_volume": cum_vol, "change_pct": (c / prev_close - 1) * 100, "minutes_since_open": elapsed,
    })
    a.levels += [Level(orh, f"{or_minutes}-min opening-range high", "opening_range"),
                 Level(orl, f"{or_minutes}-min opening-range low", "opening_range"),
                 Level(v_now, "VWAP", "vwap"), Level(hod, "high of day", "resistance"),
                 Level(prev_high, "prior-day high", "resistance"), Level(prev_close, "prior close", "support")]
    if a.metrics.get("premarket_available"):
        a.levels.append(Level(a.metrics["premarket_high"], "pre-market high", "resistance"))

    S = a.signals
    above_vwap = c > v_now
    S.append(Signal("above_vwap", "Holding above VWAP", above_vwap, f"{fmt(c)} vs VWAP {fmt(v_now)}", "trend", 1.5))
    ema_up = _f(ema9.iloc[-1]) is not None and _f(ema20.iloc[-1]) is not None and ema9.iloc[-1] > ema20.iloc[-1]
    S.append(Signal("ema_trend_5m", "5-min trend up (EMA9 > EMA20)", bool(ema_up),
                    f"EMA9 {fmt(float(ema9.iloc[-1]))} vs EMA20 {fmt(float(ema20.iloc[-1]))}"
                    if ema_up is not None and _f(ema20.iloc[-1]) else "not enough 5-min bars", "trend"))
    hl15 = len(b15) >= 3 and b15["low"].iloc[-1] >= b15["low"].iloc[-3] and b15["high"].max() >= orh
    S.append(Signal("structure_15m", "15-min structure holding higher lows", bool(hl15),
                    f"{len(b15)} fifteen-minute bars", "structure"))
    broke_orh = bool((b5["close"] > orh).any())
    S.append(Signal("orb", "Broke above the opening range", broke_orh, f"OR high {fmt(orh)}, high of day {fmt(hod)}",
                    "structure", 1.2))
    S.append(Signal("rvol", "Relative volume elevated", (rvol or 0) >= 1.2,
                    f"{rvol or 0:.1f}x typical volume for this time of day (estimate)", "volume", 1.2))
    vol_confirm = float(last5["volume"]) >= 1.2 * v5_avg and float(last5["close"]) > float(last5["open"])
    S.append(Signal("bar_volume", "Buying volume on the latest 5-min bar", vol_confirm,
                    f"{float(last5['volume']) / v5_avg:.1f}x the session's average 5-min volume", "volume"))
    if spy1 is not None and not spy1.empty:
        sreg = spy1[(spy1.index.time >= time(9, 30)) & (spy1.index <= bars1.index[-1])]
        if not sreg.empty and float(sreg["open"].iloc[0]) > 0 and opn > 0:
            s_ok = float(sreg["close"].iloc[-1]) > float(ind.vwap(sreg).iloc[-1])
            S.append(Signal("market_align", "S&P 500 above its VWAP", s_ok, "market tape supportive" if s_ok
                            else "market trading below VWAP", "relative_strength"))
            s_chg = (float(sreg["close"].iloc[-1]) / float(sreg["open"].iloc[0]) - 1) * 100
            st_chg = (c / opn - 1) * 100
            S.append(Signal("rs_intraday", "Stronger than the market today", st_chg > s_chg,
                            f"{st_chg:+.2f}% from open vs S&P {s_chg:+.2f}%", "relative_strength"))
    d_trend = None
    if "sma20" in prior and _f(prior["sma20"].iloc[-1]):
        d_trend = prev_close > float(prior["sma20"].iloc[-1])
        S.append(Signal("daily_trend", "Daily trend supportive (above 20-day)", bool(d_trend),
                        f"prior close {fmt(prev_close)} vs 20-day {fmt(float(prior['sma20'].iloc[-1]))}", "trend"))

    # ---------------------------------------------------------------- setup type & breakdown
    dipped_below = bool((recent["close"] < vw5.iloc[-6:]).any())
    reclaim = dipped_below and float(last5["close"]) > float(vw5.iloc[-1]) and above_vwap
    retest = broke_orh and pb_low <= orh + 0.25 * atr5 and pb_low >= orh - 0.75 * atr5 and c > orh
    vwap_hold = above_vwap and pb_low <= float(vw5.iloc[-1]) + 0.3 * atr5 and bool(ema_up) and hod > orh
    bd = []
    if c < v_now and c < orl:
        bd.append(f"Below VWAP {fmt(v_now)} and the opening-range low {fmt(orl)}")
    if len(b5) >= 6 and (b5["high"].iloc[-3:].diff().dropna() < 0).all() and c < v_now:
        bd.append("Lower highs on the 5-minute chart below VWAP")
    if gap >= 4 and c < opn and c < v_now:
        bd.append(f"Gap of {gap:+.1f}% fading below the open and VWAP")
    for m in bd:
        S.append(Signal("breakdown", "Breakdown warning", True, m, "breakdown"))
    if bd:
        a.technical_state = "breaking_down"
        a.reasons_avoid += bd
        return a
    if reclaim:
        stype, support = "VWAP reclaim", Level(float(vw5.iloc[-1]), "VWAP", "vwap")
    elif retest:
        stype, support = "Opening-range breakout retest", Level(orh, "opening-range high", "opening_range")
    elif vwap_hold:
        stype, support = "Pullback to VWAP in an uptrend", Level(float(vw5.iloc[-1]), "VWAP", "vwap")
    else:
        a.technical_state = "no_setup"
        a.reasons_avoid.append("No VWAP reclaim, opening-range retest or orderly VWAP pullback")
        return a
    if c > support.price + 2.5 * atr5:
        a.technical_state = "extended"
        a.reasons_avoid.append("Extended from intraday support — do not chase")
        return a

    a.is_setup = True
    a.setup_type = stype
    a.technical_state = "stabilizing"
    zone_low, zone_high = support.price - 0.25 * atr5, support.price + 0.4 * atr5
    stop = min(pb_low, support.price) - 0.25 * atr5
    triggered = prev5 is not None and float(last5["close"]) > float(prev5["high"]) and vol_confirm
    if triggered:
        trigger = float(prev5["high"])
        ttext = (f"Triggered: 5-min close {fmt(float(last5['close']))} above the prior 5-min high {fmt(trigger)} "
                 f"with volume")
    else:
        trigger = max(float(last5["high"]), zone_high)
        ttext = f"5-minute close above {fmt(trigger)} with volume above the session's 5-min average"
    entry = trigger
    hi20 = float(prior["high"].iloc[-20:].max())
    atr_proj = prev_close + d_atr if d_atr else None
    names = {hod: "high of day", prev_high: "prior-day high", a.metrics.get("premarket_high"): "pre-market high",
             orh + (orh - orl): "opening-range measured move", hi20: "20-day high",
             atr_proj: "one daily ATR above the prior close"}
    targets = sorted({x for x in names if x and x > entry + 0.5 * atr5})
    if not targets:
        a.status = "Avoid"
        a.reasons_avoid.append("No intraday resistance with room above the entry")
        return a
    t1, t2 = targets[0], targets[1] if len(targets) > 1 else None
    a.plan = TradePlan(
        entry_zone_low=zone_low, entry_zone_high=zone_high, trigger_price=trigger, trigger_text=ttext,
        entry_price=entry, stop=stop, stop_text=f"Below {fmt(stop)} (under the {support.label} and the pullback low)",
        target1=t1, target1_text=f"{names.get(t1, 'resistance').capitalize()} {fmt(t1)}",
        target2=t2, target2_text=(f"{names.get(t2, 'resistance').capitalize()} {fmt(t2)}" if t2 else None),
        conditional_text=(f"Watch the {fmt(zone_low)}–{fmt(zone_high)} area ({support.label}); consider a setup only "
                          f"after a 5-minute close above {fmt(trigger)} with confirming volume. Exit by the close; "
                          f"invalidated below {fmt(stop)}."),
    )
    a.levels += [Level(trigger, "confirmation trigger", "trigger"), Level(stop, "stop / invalidation", "stop"),
                 Level(t1, "target 1", "target")] + ([Level(t2, "target 2", "target")] if t2 else [])
    a.metrics.update({"support": support.price, "support_label": support.label, "pullback_low": pb_low})
    errs = a.plan.validity_errors()
    if errs:
        a.status = "Avoid"
        a.reasons_avoid += errs
        return a
    if _check_invalidation and len(b5) > 8:
        cut = b5.index[-1]
        earlier = analyze_intraday(bars1[bars1.index < cut], symbol, daily, or_minutes=or_minutes, spy1=spy1,
                                   _check_invalidation=False)
        if earlier.plan and earlier.status in ("Approaching Entry", "Triggered") and c < earlier.plan.stop:
            a.status, a.plan = "Invalidated", earlier.plan
            a.reasons_avoid.append(f"Traded {fmt(c)} below the earlier stop {fmt(earlier.plan.stop)}")
            return a
    if triggered:
        a.status = "Triggered"
        if c > trigger + 1.0 * atr5:
            a.warnings.append("Price has run more than 1 ATR (5-min) past the trigger — do not chase.")
    elif float(recent["low"].iloc[-2:].min()) <= zone_high + 0.2 * atr5:
        a.status = "Approaching Entry"
        a.warnings.append("At support but not confirmed — wait for the trigger.")
    else:
        a.status = "Watch"
    if d_trend is False:
        a.warnings.append("Counter to the daily trend — intraday-only trade, keep size small.")
    return a
