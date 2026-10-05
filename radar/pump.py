"""Pump-and-dump risk screen.

Flags follow the warning signs in SEC investor alerts on pump-and-dump schemes (micro-cap, low price,
sudden price/volume spikes with no news, thin float, little business behind the stock, reverse splits).
Each flag cites the measured value. `spike_aftermath` measures, from our own price history, what
actually happened after comparable spikes, so the warning is backed by outcomes, not just rules."""
import math

import numpy as np
import pandas as pd

HIGH, ELEVATED = 55, 30


def _f(x):
    try:
        v = float(x)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _spike_then_crash(close):
    """True if, in the last ~120 sessions, the stock rose >=80% off a 20-day low and then fell >=50% from that peak."""
    c = close.iloc[-120:]
    if len(c) < 30:
        return False
    low20 = c.rolling(20, min_periods=5).min()
    run = c / low20 - 1
    for i in np.where(run.to_numpy() >= 0.8)[0]:
        peak = c.iloc[i]
        if (c.iloc[i:] / peak - 1).min() <= -0.5:
            return True
    return False


def assess(f, info, news_items, reverse_split_date):
    """f: feature frame for the stock (last row = latest). Returns dict(score, level, flags)."""
    r = f.iloc[-1]
    flags = []

    def add(pts, label, value):
        flags.append({"points": pts, "label": label, "value": value})

    price = _f(r["close"])
    mcap = _f(info.get("marketCap"))
    ret1, ret5, ret20 = _f(r["ret1"]) or 0, _f(r["ret5"]) or 0, _f(r["ret20"]) or 0
    rv = math.exp(_f(r["rel_volume"]) or 0)
    vol = _f(r["volume"]) or 0
    flt = _f(info.get("floatShares")) or _f(info.get("sharesOutstanding"))

    if mcap is not None:
        if mcap < 300e6:
            add(20, "Micro-cap company", f"${mcap / 1e6:.0f}M market value")
        elif mcap < 2e9:
            add(8, "Small-cap company", f"${mcap / 1e9:.2f}B market value")
    if price is not None and price < 5:
        add(20 if price < 1 else 15, "Penny-stock price", f"${price:.2f}")
    if ret5 >= 50 or ret20 >= 100:
        add(25, "Parabolic price spike", f"{ret5:+.0f}% in 5 days, {ret20:+.0f}% in 20 days")
    elif ret5 >= 25:
        add(10, "Sharp short-term run-up", f"{ret5:+.0f}% in 5 days")
    if rv >= 10:
        add(20, "Volume explosion", f"{rv:.0f}× normal volume")
    elif rv >= 5:
        add(15, "Volume surge", f"{rv:.1f}× normal volume")
    if flt:
        if vol / flt >= 1:
            add(15, "Entire float traded in one day", f"{vol / flt:.1f}× the float changed hands")
        if flt < 20e6:
            add(10, "Very small float", f"{flt / 1e6:.1f}M tradable shares")
    pm, rev = _f(info.get("profitMargins")), _f(info.get("totalRevenue"))
    if pm is not None and pm < 0:
        add(8, "Loses money", f"{pm * 100:.0f}% profit margin")
    if rev is not None and rev < 50e6:
        add(8, "Little revenue", f"${rev / 1e6:.1f}M a year")
    if (info.get("numberOfAnalystOpinions") or 0) <= 1:
        add(7, "Almost no analyst coverage", f"{info.get('numberOfAnalystOpinions') or 0} analysts")
    inst = _f(info.get("heldPercentInstitutions"))
    if inst is not None and inst < 0.10:
        add(8, "Few institutional owners", f"{inst * 100:.1f}% held by institutions")
    if reverse_split_date:
        add(15, "Reverse split in the last year", reverse_split_date)
    if abs(ret1) >= 20 and not news_items:
        add(10, "Huge move with no company news", f"{ret1:+.0f}% today, no headlines naming it")
    dist50 = _f(r["dist_sma50"])
    if dist50 is not None and dist50 >= 50:
        add(10, "Far above its 50-day average", f"{dist50:+.0f}%")
    if _spike_then_crash(f["close"]):
        add(10, "Earlier spike-and-crash pattern", "rose 80%+ then lost half within ~6 months")

    score = sum(x["points"] for x in flags)
    # Pumps need a small, thinly traded stock: large companies are too expensive to move this way.
    if mcap is not None and mcap >= 10e9:
        score = min(score, ELEVATED - 1)
    elif mcap is not None and mcap >= 2e9:
        score = round(score * 0.7)
    score = min(100, score)
    level = "high" if score >= HIGH else "elevated" if score >= ELEVATED else "low"
    flags.sort(key=lambda x: -x["points"])
    return {"score": score, "level": level, "flags": flags}


def spike_aftermath(hist):
    """For every past spike in our price history (+40% in 5 days on >=3x volume, price under $20),
    what happened over the next 20 sessions? hist: {symbol: OHLCV DataFrame}."""
    fwd = []
    for sym, df in hist.items():
        if sym.startswith("^") or len(df) < 60:
            continue
        c, v = df["Close"], df["Volume"].astype(float)
        spike = (c.pct_change(5) >= 0.40) & (v / v.shift(1).rolling(20).mean() >= 3) & (c < 20)
        fwd20 = (c.shift(-20) / c - 1) * 100
        last = -10 ** 9
        for i in np.where(spike.fillna(False).to_numpy())[0]:
            if i - last < 20:
                continue  # one event per run-up
            last = i
            if not pd.isna(fwd20.iloc[i]):
                fwd.append(fwd20.iloc[i])
    if not fwd:
        return None
    a = np.array(fwd)
    return {"n": int(len(a)), "median_20d": float(np.median(a)), "pct_lost_30": float((a <= -30).mean()),
            "pct_lost_any": float((a < 0).mean()), "pct_gained_20": float((a >= 20).mean())}
