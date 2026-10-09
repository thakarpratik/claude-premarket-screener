"""Causal technical indicators. Every value at bar t uses only bars <= t (no look-ahead).

Bars are DataFrames with lowercase columns open, high, low, close, volume and a tz-aware DatetimeIndex."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP over the frame (pass one session's regular-hours bars)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    v = df["volume"].astype(float)
    cv = v.cumsum()
    return (tp * v).cumsum() / cv.replace(0, np.nan)


def accumulation_distribution(df: pd.DataFrame) -> pd.Series:
    rng = (df["high"] - df["low"]).replace(0, np.nan)
    mfm = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / rng
    return (mfm.fillna(0) * df["volume"]).cumsum()


def pivots(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """Swing highs/lows confirmed k bars later. Column `*_confirmed_at` is the bar index (position) at
    which the pivot becomes knowable, so callers at bar t may only use pivots confirmed at <= t."""
    h, l_ = df["high"].to_numpy(), df["low"].to_numpy()
    rows = []
    for i in range(k, len(df) - k):
        win_h, win_l = h[i - k:i + k + 1], l_[i - k:i + k + 1]
        if h[i] == win_h.max() and (win_h[:k] < h[i]).all():
            rows.append({"pos": i, "kind": "high", "price": float(h[i]), "confirmed_at": i + k})
        if l_[i] == win_l.min() and (win_l[:k] > l_[i]).all():
            rows.append({"pos": i, "kind": "low", "price": float(l_[i]), "confirmed_at": i + k})
    return pd.DataFrame(rows, columns=["pos", "kind", "price", "confirmed_at"])


def add_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["open", "high", "low", "close", "volume"]].copy()
    out.attrs.update(df.attrs)
    c = out["close"]
    out["sma20"], out["sma50"], out["sma200"] = sma(c, 20), sma(c, 50), sma(c, 200)
    out["ema10"] = ema(c, 10)
    out["rsi14"] = rsi(c, 14)
    out = out.join(macd(c))
    out["atr14"] = atr(out, 14)
    out["avg_vol20"] = out["volume"].rolling(20, min_periods=10).mean().shift(1)  # prior 20 sessions
    out["dollar_vol20"] = (out["close"] * out["volume"]).rolling(20, min_periods=10).mean()
    out["rel_vol"] = out["volume"] / out["avg_vol20"]
    out["ad_line"] = accumulation_distribution(out)
    out.attrs = dict(df.attrs)  # joins can drop attrs; keep e.g. the partial-last-bar flag
    return out


def slope(s: pd.Series, n: int) -> float | None:
    """Percent change of a series over the last n bars (e.g. how fast a moving average is rising)."""
    s = s.dropna()
    if len(s) <= n or s.iloc[-n - 1] == 0:
        return None
    return float((s.iloc[-1] / s.iloc[-n - 1] - 1) * 100)


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if df.empty:
        return df
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(f"{minutes}min", label="left", closed="left", origin="start_day", offset="30min").agg(agg)
    return out.dropna(subset=["open"])


def weekly(df: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample("W-FRI").agg(agg).dropna(subset=["open"])
