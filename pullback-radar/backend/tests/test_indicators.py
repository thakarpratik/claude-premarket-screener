import numpy as np
import pandas as pd

from pullback_radar import indicators as ind


def frame(close, vol=1000):
    idx = pd.date_range("2026-01-01", periods=len(close), freq="D", tz="America/New_York")
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c, "volume": vol}, index=idx)


def test_sma_and_rsi_bounds():
    c = pd.Series(np.arange(1, 41, dtype=float))
    assert ind.sma(c, 5).iloc[-1] == 38
    r = ind.rsi(c, 14)
    assert r.iloc[-1] == 100  # only gains
    r2 = ind.rsi(pd.Series(np.linspace(50, 10, 40)), 14)
    assert r2.iloc[-1] < 1


def test_vwap_volume_weighted():
    df = frame([10, 20], vol=[100, 300])
    v = ind.vwap(df)
    assert abs(v.iloc[-1] - (10 * 100 + 20 * 300) / 400) < 1e-9


def test_atr_positive_and_causal():
    df = frame(list(np.linspace(10, 20, 60)))
    a = ind.atr(df).dropna()
    assert (a > 0).all()
    longer = pd.concat([df, frame([500] * 5).set_index(pd.date_range("2026-03-02", periods=5, tz="America/New_York"))])
    assert ind.atr(longer).iloc[59] == ind.atr(df).iloc[59]


def test_pivots_confirmed_only_after_k_bars():
    close = [10, 11, 12, 15, 12, 11, 10, 11, 12, 13]
    df = frame(close)
    p = ind.pivots(df, 3)
    hi = p[p["kind"] == "high"].iloc[0]
    assert hi["pos"] == 3 and hi["confirmed_at"] == 6


def test_indicators_ignore_existing_columns():
    df = ind.add_daily_indicators(frame(list(np.linspace(10, 30, 80))))
    again = ind.add_daily_indicators(df)  # must not fail on already-present indicator columns
    assert "macd_hist" in again
