"""Swing pullback detector (daily + weekly bars, holding period ~2-10 sessions).

Looks for a stock in a healthy uptrend that has pulled back toward a meaningful support (rising 20/50-day
average, prior breakout level, confirmed higher low) on lighter volume, and separates that from a stock that
is breaking down. Only bars up to the evaluated bar are used, and swing pivots are used only once confirmed,
so the same function can be replayed bar-by-bar in the backtester without look-ahead."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .. import indicators as ind
from .common import Level, SetupAnalysis, Signal, TradePlan, fmt

MIN_BARS = 60
PIVOT_K = 3


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _ret(close: pd.Series, n: int) -> float | None:
    if len(close) <= n:
        return None
    return float((close.iloc[-1] / close.iloc[-n - 1] - 1) * 100)


def _candle(row, prev) -> tuple[str | None, bool]:
    """Bullish reversal candle name and whether the bar closed strong (top third of range)."""
    o, h, l_, c = row["open"], row["high"], row["low"], row["close"]
    rng = h - l_
    if rng <= 0:
        return None, False
    body = abs(c - o)
    strong = (c - l_) / rng >= 0.6
    lower_wick = min(o, c) - l_
    if lower_wick >= 2 * body and strong and lower_wick / rng >= 0.45:
        return "hammer", strong
    if prev is not None and c > o and prev["close"] < prev["open"] and c >= prev["open"] and o <= prev["close"]:
        return "bullish engulfing", strong
    if prev is not None and c > prev["high"] and c > o:
        return "close above prior high", strong
    return None, strong


def analyze_swing(f: pd.DataFrame, symbol: str, *, bench: pd.DataFrame | None = None,
                  sector: pd.DataFrame | None = None, pivots: pd.DataFrame | None = None,
                  _check_invalidation: bool = True) -> SetupAnalysis:
    """f: daily bars with indicators (indicators.add_daily_indicators) ending at the evaluated bar."""
    a = SetupAnalysis(symbol, "swing", False, "Avoid", "insufficient_data", None, None, None)
    if f is None or len(f) < MIN_BARS or pd.isna(f["atr14"].iloc[-1]) or pd.isna(f["sma50"].iloc[-1]):
        a.reasons_avoid.append(f"Not enough daily history (need {MIN_BARS}+ sessions)")
        return a
    t = len(f) - 1
    r = f.iloc[-1]
    prev = f.iloc[-2]
    c, atr = float(r["close"]), float(r["atr14"])
    a.price, a.as_of = c, f.index[-1].isoformat()
    sma20, sma50, sma200 = _f(r["sma20"]), _f(r["sma50"]), _f(r["sma200"])
    rsi = _f(r["rsi14"])
    atr_pct = atr / c * 100
    if pivots is None:
        pivots = ind.pivots(f, PIVOT_K)
    pv = pivots[pivots["confirmed_at"] <= t]

    # ---------------------------------------------------------------- swing structure
    look = f.iloc[-20:]
    hi_pos = t - 19 + int(np.argmax(look["high"].to_numpy()))
    swing_high = float(f["high"].iloc[hi_pos])
    days_since_high = t - hi_pos
    pb = f.iloc[hi_pos:]
    pullback_low = float(pb["low"].min())
    pb_low_pos = hi_pos + int(np.argmin(pb["low"].to_numpy()))
    adv = f.iloc[max(0, hi_pos - 25):hi_pos + 1]
    adv_low = float(adv["low"].min())
    adv_low_pos = max(0, hi_pos - 25) + int(np.argmin(adv["low"].to_numpy()))
    advance = swing_high - adv_low
    depth_pct = (swing_high - c) / swing_high * 100
    max_depth_pct = (swing_high - pullback_low) / swing_high * 100
    retrace = (swing_high - pullback_low) / advance if advance > 0 else None
    pb_bars = f.iloc[hi_pos + 1:t] if t - hi_pos >= 2 else f.iloc[hi_pos + 1:t + 1]
    adv_bars = f.iloc[adv_low_pos:hi_pos + 1]
    vol_contraction = (float(pb_bars["volume"].mean()) / float(adv_bars["volume"].mean())
                       if len(pb_bars) and len(adv_bars) and adv_bars["volume"].mean() > 0 else None)
    rsi_peak = _f(f["rsi14"].iloc[adv_low_pos:hi_pos + 1].max())
    rel_vol = _f(r["rel_vol"])
    day_ret = (c / float(prev["close"]) - 1) * 100

    # ---------------------------------------------------------------- support candidates
    cands: list[Level] = []
    if sma20:
        cands.append(Level(sma20, "20-day average", "ma"))
    if sma50:
        cands.append(Level(sma50, "50-day average", "ma"))
    ema10 = _f(r["ema10"])
    if ema10:
        cands.append(Level(ema10, "10-day EMA", "ma"))
    # Prior breakout: highest confirmed pivot high formed before the advance that price later cleared.
    before_adv = pv[(pv["kind"] == "high") & (pv["pos"] < adv_low_pos + 3) & (pv["pos"] >= t - 120)]
    cleared = before_adv[before_adv["price"] < swing_high * 0.985]
    if not cleared.empty:
        bo = float(cleared.sort_values("pos").iloc[-1]["price"])
        cands.append(Level(bo, "prior breakout level", "breakout"))
    lows = pv[pv["kind"] == "low"].sort_values("pos")
    if not lows.empty:
        cands.append(Level(float(lows.iloc[-1]["price"]), "last confirmed swing low", "pivot"))
    if days_since_high >= 2:
        cands.append(Level(pullback_low, "pullback low", "pivot"))
    near = [lv for lv in cands if pullback_low - 0.75 * atr <= lv.price <= c + 0.25 * atr]
    support = None
    confluence = []
    if near:
        # Prefer structural levels (averages, breakout, swing low); the raw pullback low is the fallback.
        structural = [lv for lv in near if lv.label != "pullback low"]
        support = min(structural or near, key=lambda lv: abs(lv.price - pullback_low))
        confluence = [lv for lv in near if abs(lv.price - support.price) <= 0.5 * atr]
    a.levels.extend(cands)
    a.levels.append(Level(swing_high, "recent swing high", "resistance"))
    hi120 = float(f["high"].iloc[-min(len(f), 252):].max())
    a.levels.append(Level(hi120, "52-week high" if len(f) >= 252 else f"{min(len(f), 252)}-day high",
                          "resistance"))

    # ---------------------------------------------------------------- signals
    S = a.signals
    sma50_slope = ind.slope(f["sma50"], 10)
    sma20_slope = ind.slope(f["sma20"], 5)
    above_50 = c > sma50
    trend_ok = above_50 and (sma50_slope or 0) > 0 and (sma200 is None or sma50 > sma200)
    S.append(Signal("above_rising_50", "Above a rising 50-day average", bool(above_50 and (sma50_slope or 0) > 0),
                    f"close {fmt(c)} vs 50-day {fmt(sma50)}, 50-day slope {sma50_slope or 0:+.2f}%/10d", "trend", 1.5))
    S.append(Signal("rising_20", "20-day average rising", (sma20_slope or 0) > 0,
                    f"20-day slope {sma20_slope or 0:+.2f}%/5d", "trend"))
    if sma200 is not None:
        S.append(Signal("50_over_200", "50-day above 200-day", sma50 > sma200,
                        f"50-day {fmt(sma50)} vs 200-day {fmt(sma200)}", "trend", 1.2))
    wk = ind.weekly(f[["open", "high", "low", "close", "volume"]])
    wk_ok = None
    if len(wk) >= 12:
        wk10 = wk["close"].rolling(10).mean()
        wk_ok = bool(wk["close"].iloc[-1] > wk10.iloc[-1] and wk10.iloc[-1] > wk10.iloc[-4])
        S.append(Signal("weekly_trend", "Weekly trend up", wk_ok,
                        f"weekly close {fmt(float(wk['close'].iloc[-1]))} vs rising 10-week avg {fmt(float(wk10.iloc[-1]))}",
                        "trend"))
    healthy_depth = 2 <= max_depth_pct <= 15 and (retrace is None or retrace <= 0.618) and 2 <= days_since_high <= 15
    S.append(Signal("healthy_depth", "Orderly pullback depth", healthy_depth,
                    f"{max_depth_pct:.1f}% off the high over {days_since_high} sessions"
                    + (f", {retrace * 100:.0f}% retracement of the advance" if retrace is not None else ""), "pullback", 1.5))
    near_support = support is not None and abs(c - support.price) <= 1.0 * atr
    S.append(Signal("near_support", "At or near support", near_support,
                    (f"{fmt(c)} is {(c - support.price) / atr:+.1f} ATR from the {support.label} {fmt(support.price)}"
                     if support else "no technically meaningful support near price"), "structure", 1.5))
    S.append(Signal("confluence", "Support confluence", len(confluence) >= 2,
                    ", ".join(lv.label for lv in confluence) if confluence else "single level", "structure"))
    rsi_reset = rsi is not None and rsi_peak is not None and rsi_peak >= 60 and 38 <= rsi <= 58
    S.append(Signal("rsi_reset", "RSI reset from elevated levels", rsi_reset,
                    f"RSI {rsi or 0:.0f} after peaking at {rsi_peak or 0:.0f} during the advance", "pullback"))
    mh, mh1 = _f(r["macd_hist"]), _f(prev["macd_hist"])
    macd_up = mh is not None and mh1 is not None and mh > mh1
    S.append(Signal("macd_stabilising", "MACD momentum stabilising", macd_up,
                    f"MACD histogram {mh or 0:+.3f} vs {mh1 or 0:+.3f} prior", "confirmation"))
    candle, strong_close = _candle(r, prev)
    S.append(Signal("reversal_candle", "Bullish reversal candle", candle is not None,
                    candle or "no reversal pattern on the latest bar", "confirmation", 1.2))
    vc_ok = vol_contraction is not None and vol_contraction < 0.85
    S.append(Signal("volume_dryup", "Lighter volume on the pullback", vc_ok,
                    (f"pullback volume {vol_contraction:.2f}x the advance" if vol_contraction is not None
                     else "not measurable"), "volume", 1.2))
    vol_return = rel_vol is not None and rel_vol >= 1.2 and c > float(r["open"])
    S.append(Signal("buying_volume", "Buying volume returning", vol_return,
                    f"today {rel_vol or 0:.1f}x average volume on an {'up' if c > r['open'] else 'down'} bar", "volume"))
    higher_low = len(lows) >= 2 and lows.iloc[-1]["price"] > lows.iloc[-2]["price"]
    if len(lows) >= 2:
        S.append(Signal("higher_low", "Higher low in place", bool(higher_low),
                        f"last swing low {fmt(float(lows.iloc[-1]['price']))} vs prior {fmt(float(lows.iloc[-2]['price']))}",
                        "structure"))
    ad = f["ad_line"]
    ad_ok = len(ad) > 21 and ad.iloc[-1] > ad.iloc[-21]
    S.append(Signal("accumulation", "Accumulation over 20 sessions", bool(ad_ok),
                    "accumulation/distribution line " + ("rising" if ad_ok else "falling"), "volume", 0.8))
    rs = {}
    for name, b in (("market", bench), ("sector", sector)):
        if b is None or b.empty:
            continue
        bb = b[b.index <= f.index[-1]]["close"]
        for n in (20, 63):
            sr, br = _ret(f["close"], n), _ret(bb, n)
            if sr is not None and br is not None:
                rs[f"rs_{name}_{n}"] = sr - br
    if "rs_market_63" in rs or "rs_market_20" in rs:
        v = rs.get("rs_market_63", rs.get("rs_market_20"))
        S.append(Signal("rs_market", "Outperforming the market", v > 0, f"{v:+.1f} pts vs S&P 500 over 3 months",
                        "relative_strength", 1.2))
    if "rs_sector_63" in rs or "rs_sector_20" in rs:
        v = rs.get("rs_sector_63", rs.get("rs_sector_20"))
        S.append(Signal("rs_sector", "Outperforming its sector", v > 0, f"{v:+.1f} pts vs sector ETF over 3 months",
                        "relative_strength"))

    # ---------------------------------------------------------------- breakdown detection
    bd = []
    if c < sma50 - 0.5 * atr:
        bd.append(f"Closed {fmt(c)}, more than 0.5 ATR below the 50-day average {fmt(sma50)}")
    if support is not None and c < support.price - 0.5 * atr and (rel_vol or 0) >= 1.3:
        bd.append(f"Broke the {support.label} {fmt(support.price)} on {rel_vol:.1f}x volume")
    if max_depth_pct > 20 or (retrace is not None and retrace > 0.786):
        bd.append(f"Pullback too deep ({max_depth_pct:.0f}% off the high)")
    prior_lows = lows[lows["pos"] < hi_pos]
    if not prior_lows.empty and pullback_low < float(prior_lows.iloc[-1]["price"]) - 0.1 * atr:
        bd.append(f"Undercut the prior swing low {fmt(float(prior_lows.iloc[-1]['price']))} (lower low)")
    if day_ret <= -1.25 * atr_pct and (rel_vol or 0) >= 2:
        bd.append(f"Heavy distribution day ({day_ret:+.1f}% on {rel_vol:.1f}x volume)")
    if sma200 is not None and c < sma200:
        bd.append(f"Below the 200-day average {fmt(sma200)}")
    for msg in bd:
        S.append(Signal("breakdown", "Breakdown warning", True, msg, "breakdown"))

    extended = c > (sma20 or c) + 2.5 * atr or (days_since_high == 0 and depth_pct < 1.0)
    a.metrics.update({
        "atr": atr, "atr_pct": atr_pct, "rsi": rsi, "rsi_peak": rsi_peak, "macd_hist": mh,
        "sma20": sma20, "sma50": sma50, "sma200": sma200, "swing_high": swing_high, "pullback_low": pullback_low,
        "pullback_depth_pct": max_depth_pct, "depth_from_high_pct": depth_pct, "retracement": retrace,
        "days_since_high": days_since_high, "volume_contraction": vol_contraction, "rel_volume": rel_vol,
        "dollar_volume_20d": _f(r["dollar_vol20"]), "day_change_pct": day_ret,
        "support": support.price if support else None, "support_label": support.label if support else None,
        "weekly_trend_up": wk_ok, **rs,
    })

    # ---------------------------------------------------------------- classification
    # A plan that was active on the prior bar and whose stop was taken out is invalidated.
    if _check_invalidation and len(f) > MIN_BARS + 1:
        prior = analyze_swing(f.iloc[:-1], symbol, bench=bench, sector=sector, pivots=pivots,
                              _check_invalidation=False)
        prior_firm = prior.status in ("Approaching Entry", "Triggered") or (
            prior.status == "Watch" and prior.metrics.get("support_label") != "pullback low")
        if prior.plan and prior_firm and c < prior.plan.stop:
            a.is_setup, a.status, a.plan = True, "Invalidated", prior.plan
            a.technical_state = "breaking_down" if bd else "invalidated"
            a.setup_type = prior.setup_type
            a.reasons_avoid.append(f"Closed {fmt(c)} below the prior plan's stop {fmt(prior.plan.stop)}")
            a.reasons_avoid += bd
            return a
    if len(bd) >= 2 or any("Broke the" in b for b in bd):
        a.technical_state = "breaking_down"
        a.reasons_avoid += bd
        a.status = "Avoid"
        a.is_setup = True  # shown as Avoid so users can see why a familiar name dropped out
        return a
    if not trend_ok:
        a.technical_state = "no_trend"
        a.reasons_avoid.append("No healthy uptrend (needs price above a rising 50-day average"
                               + (", 50-day above 200-day)" if sma200 is not None else ")"))
        return a
    if extended:
        a.technical_state = "extended"
        a.reasons_avoid.append("Extended above support — no pullback to buy yet; do not chase")
        return a
    if not healthy_depth or support is None:
        a.technical_state = "no_pullback" if max_depth_pct < 2 else "irregular_pullback"
        a.reasons_avoid.append("No orderly pullback into a meaningful support area")
        return a

    a.is_setup = True
    a.setup_type = f"Pullback to {support.label}"
    a.technical_state = "stabilizing" if (candle or macd_up) else "healthy_pullback"
    zone_low = support.price - 0.25 * atr
    zone_high = support.price + 0.5 * atr
    prior_high = float(prev["high"])
    stop = min(pullback_low, support.price) - 0.35 * atr
    anchor = support.label if support.label == "pullback low" else f"{support.label} and the pullback low"
    stop_text = f"Daily close below {fmt(stop)} (below the {anchor}, with a 0.35 ATR buffer)"
    touched_zone = float(f["low"].iloc[-3:].min()) <= zone_high + 0.25 * atr
    triggered = touched_zone and c > prior_high and (candle is not None or strong_close) and (rel_vol or 0) >= 1.0
    if triggered:
        trigger = prior_high
        trigger_text = (f"Triggered: closed {fmt(c)} above the prior session high {fmt(prior_high)} "
                        f"on {rel_vol:.1f}x average volume")
    else:
        trigger = max(float(r["high"]), zone_low)
        trigger_text = (f"Daily close above {fmt(trigger)} (latest session high) with volume at least "
                        f"1.0x the 20-day average")
    entry = trigger
    t1 = swing_high
    t1_text = f"Retest of the recent swing high {fmt(swing_high)}"
    if t1 <= entry + 0.5 * atr:
        t1 = max(hi120, entry + 1.0 * atr) if hi120 > entry + 0.5 * atr else None
        t1_text = f"Prior resistance at the {hi120 and fmt(hi120)} high" if t1 else ""
    t2, t2_text = None, None
    if t1:
        proj = swing_high + 0.618 * (swing_high - pullback_low)
        if hi120 > t1 + 0.5 * atr:
            t2, t2_text = hi120, f"Prior high {fmt(hi120)}"
        elif proj > t1:
            t2, t2_text = proj, f"Measured-move projection {fmt(proj)} (61.8% of the pullback added to the high)"
    if t1 is None:
        a.status = "Avoid"
        a.reasons_avoid.append("No overhead target with room above the entry")
        return a
    a.plan = TradePlan(
        entry_zone_low=zone_low, entry_zone_high=zone_high, trigger_price=trigger, trigger_text=trigger_text,
        entry_price=entry, stop=stop, stop_text=stop_text, target1=t1, target1_text=t1_text, target2=t2,
        target2_text=t2_text,
        conditional_text=(f"Watch the {fmt(zone_low)}–{fmt(zone_high)} support zone ({support.label}); consider a "
                          f"setup only after price closes above {fmt(trigger)} with confirming volume. "
                          f"Invalidated below {fmt(stop)}."),
    )
    a.levels += [Level(zone_low, "entry zone low", "support"), Level(zone_high, "entry zone high", "support"),
                 Level(trigger, "confirmation trigger", "trigger"), Level(stop, "stop / invalidation", "stop"),
                 Level(t1, "target 1", "target")] + ([Level(t2, "target 2", "target")] if t2 else [])
    errs = a.plan.validity_errors()
    if errs:
        a.status = "Avoid"
        a.reasons_avoid += errs
        return a

    if c < stop:
        a.status = "Invalidated"
        a.reasons_avoid.append(f"Price {fmt(c)} is below the invalidation level {fmt(stop)}")
    elif triggered and f.attrs.get("partial_last_bar"):
        a.status = "Approaching Entry"
        a.warnings.append(f"Trading above the {fmt(trigger)} trigger intraday, but the daily bar is still forming — "
                          "confirmation requires the close.")
    elif triggered:
        a.status = "Triggered"
        if c > trigger + 0.75 * atr:
            a.warnings.append(f"Price is {(c - trigger) / atr:.1f} ATR above the trigger — chasing worsens the "
                              "reward/risk; wait for a retest")
    elif support.label == "pullback low" and pb_low_pos == t:
        a.status = "Watch"
        a.warnings.append("Still printing new lows inside the pullback — no support has held yet.")
    elif float(f["low"].iloc[-2:].min()) <= zone_high + 0.25 * atr:
        a.status = "Approaching Entry"
    else:
        a.status = "Watch"
    if a.status != "Triggered":
        a.warnings.append("Near support is not a buy signal by itself — wait for the confirmation trigger.")
    return a
