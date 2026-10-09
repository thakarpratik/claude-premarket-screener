from datetime import date, datetime

import pandas as pd
import pytest

from pullback_radar.config import EnvSettings
from pullback_radar.market_calendar import ET
from pullback_radar.providers import build_hub
from pullback_radar.services.backtest import BTParams, BTTrade, _simulate_exit, metrics, run_backtest


def bars(rows):
    idx = pd.date_range("2026-01-05", periods=len(rows), freq="B", tz="America/New_York")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_stop_assumed_before_target_on_same_bar():
    b = bars([(100, 112, 94, 105)])
    px, i, reason = _simulate_exit(b, 0, 100, 95, 110, None, 0.0, 10)
    assert reason == "stop" and px == 95


def test_gap_through_stop_fills_at_open():
    b = bars([(100, 101, 99, 100), (90, 91, 88, 89)])
    px, i, reason = _simulate_exit(b, 0, 100, 95, 110, None, 0.0, 10)
    assert reason == "stop" and px == 90 and i == 1


def test_partial_at_t1_then_breakeven():
    b = bars([(100, 101, 99, 100), (101, 111, 100.5, 110), (109, 109, 99, 99)])
    px, i, reason = _simulate_exit(b, 0, 100, 95, 110, 120, 0.0, 10)
    assert reason == "breakeven stop" and px == pytest.approx((110 + 100) / 2)


def test_time_exit():
    b = bars([(100, 101, 99, 100)] * 5)
    _, i, reason = _simulate_exit(b, 0, 100, 95, 110, None, 0.0, 3)
    assert reason == "time exit" and i == 2


def test_metrics():
    def t(r):
        return BTTrade("X", "", "", 1, "2026-01-0%dT00:00:00" % (1 + len(rs)), 1, "", 0, 0, 1, r, r, r, "bull",
                       "in_sample", None, 1)
    rs = []
    trades = []
    for r in (2, -1, -1, 3):
        trades.append(t(r))
        rs.append(r)
    m = metrics(trades, 1.0)
    assert m["trades"] == 4 and m["win_rate"] == 50 and m["expectancy_r"] == 0.75 and m["profit_factor"] == 2.5


def test_demo_backtest_runs_and_labels_synthetic():
    hub = build_hub(EnvSettings(data_mode="demo"), demo_now=datetime(2026, 10, 8, 18, 0, tzinfo=ET))
    r = run_backtest(hub, ["DEMO01", "DEMO04"], BTParams(), date(2026, 3, 1), date(2026, 10, 8))
    assert r["ok"] and r["synthetic"]
    assert any(w.startswith("SYNTHETIC") for w in r["warnings"])
    for tr in r["trades"]:
        assert tr["entry_time"] > tr["signal_time"]  # fills strictly after the signal bar
    assert set(r["by_period"]) <= {"in_sample", "out_of_sample"}
