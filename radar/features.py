"""Turns daily OHLCV bars into model features. Every feature is computed from the bars available
on that day, so the same code produces both the historical training set and today's live row."""
import numpy as np
import pandas as pd

from .config import BIG_MOVE_PCT

# Features for "will the next session move big (either way)?"
BIG_FEATURES = [
    "log_atr_pct", "rv20", "vol_expansion", "rel_volume", "abs_ret", "abs_gap", "range_vs_atr",
    "squeeze", "rsi_extreme", "from_52w_high", "from_52w_low", "earnings_next", "earnings_today",
    "mkt_abs_ret", "vix", "streak",
]
# Features for "if it moves, which way?"
DIR_FEATURES = [
    "ret1", "ret5", "ret20", "rsi_c", "dist_sma20", "dist_sma50", "gap", "close_loc",
    "mkt_ret", "rel_strength_20", "earnings_next", "rel_volume_signed",
]


def _rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def earnings_flags(index, earnings_ts):
    """earnings_ts: list of pandas Timestamps (US/Eastern) of earnings releases.
    Reaction day = same day if released before noon, otherwise the next trading day.
    Returns (earnings_next, earnings_today) series aligned to `index`."""
    nxt = pd.Series(0.0, index=index)
    today = pd.Series(0.0, index=index)
    if not earnings_ts:
        return nxt, today
    days = pd.DatetimeIndex(index)
    # Trading calendar = historical bars + business days after the last bar (for upcoming events).
    future = pd.bdate_range(days[-1] + pd.Timedelta(days=1), periods=90)
    cal = days.append(future)
    for ts in earnings_ts:
        d = pd.Timestamp(ts.date())
        pos = cal.searchsorted(d)
        if pos >= len(cal):
            continue
        if cal[pos] == d and ts.hour >= 12:
            pos += 1  # after-close release -> next session reacts
        if pos >= len(cal):
            continue
        react = cal[pos]
        if react in today.index:
            today.loc[react] = 1.0
        i = cal.get_loc(react)
        if i > 0 and cal[i - 1] in nxt.index:
            nxt.loc[cal[i - 1]] = 1.0
    return nxt, today


def build(df, mkt, earnings_ts, sector_close=None):
    """df: OHLCV for one symbol. mkt: DataFrame with spy_close, vix columns aligned by date.
    Returns DataFrame with feature columns + targets (next_ret, y_big, y_up)."""
    df = df.dropna(subset=["Close"]).copy()
    c, o, h, l, v = df.Close, df.Open, df.High, df.Low, df.Volume.astype(float)
    prev = c.shift(1)
    ret = c.pct_change()
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    sma20, sma50, sma200 = c.rolling(20).mean(), c.rolling(50).mean(), c.rolling(200).mean()
    rsi = _rsi(c)
    m = mkt.reindex(df.index).ffill()
    spy_ret = m["spy_close"].pct_change()

    f = pd.DataFrame(index=df.index)
    f["log_atr_pct"] = np.log((atr / c * 100).clip(lower=0.05))
    f["rv20"] = ret.rolling(20).std() * 100
    f["vol_expansion"] = np.log((ret.rolling(5).std() + 1e-4) / (ret.rolling(60).std() + 1e-4))
    f["rel_volume"] = np.log((v / v.shift(1).rolling(20).mean()).clip(lower=0.05))
    f["abs_ret"] = ret.abs() * 100
    f["abs_gap"] = (o / prev - 1).abs() * 100
    f["range_vs_atr"] = (h - l) / atr.shift(1)
    bbw = 4 * c.rolling(20).std() / sma20
    f["squeeze"] = bbw.rolling(120, min_periods=60).rank(pct=True)
    f["rsi_extreme"] = (rsi - 50).abs()
    f["from_52w_high"] = (c / h.rolling(252, min_periods=60).max() - 1) * 100
    f["from_52w_low"] = np.log(c / l.rolling(252, min_periods=60).min())
    f["earnings_next"], f["earnings_today"] = earnings_flags(df.index, earnings_ts)
    f["mkt_abs_ret"] = spy_ret.abs() * 100
    f["vix"] = m["vix"]
    big_day = (ret.abs() * 100 >= BIG_MOVE_PCT).astype(float)
    f["streak"] = big_day.rolling(10).sum()  # big-move days in the last 2 weeks

    f["ret1"] = ret * 100
    f["ret5"] = c.pct_change(5) * 100
    f["ret20"] = c.pct_change(20) * 100
    f["rsi_c"] = rsi - 50
    f["dist_sma20"] = (c / sma20 - 1) * 100
    f["dist_sma50"] = (c / sma50 - 1) * 100
    f["gap"] = (o / prev - 1) * 100
    f["close_loc"] = ((c - l) / (h - l).replace(0, np.nan)) - 0.5
    f["mkt_ret"] = spy_ret * 100
    f["rel_strength_20"] = f["ret20"] - m["spy_close"].pct_change(20) * 100
    f["rel_volume_signed"] = f["rel_volume"] * np.sign(ret)

    # Context (not model inputs) used by the UI.
    f["close"], f["prev_close"], f["open"], f["high"], f["low"], f["volume"] = c, prev, o, h, l, v
    f["atr_pct"] = atr / c * 100
    f["sma50"], f["sma200"] = sma50, sma200
    f["rsi"] = rsi
    f["ret63"] = c.pct_change(63) * 100
    f["spy_ret63"] = m["spy_close"].pct_change(63) * 100
    f["avg_vol20"] = v.shift(1).rolling(20).mean()
    if sector_close is not None:
        f["sector_ret"] = sector_close.reindex(df.index).ffill().pct_change() * 100

    nr = c.shift(-1) / c - 1
    f["next_ret"] = nr * 100
    f["y_big"] = (nr.abs() * 100 >= BIG_MOVE_PCT).astype(float).where(nr.notna())
    f["y_up"] = (nr > 0).astype(float).where(nr.notna())
    return f
