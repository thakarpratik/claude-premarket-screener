import pandas as pd
import pytest

from pullback_radar import indicators as ind
from pullback_radar.scanner.swing import analyze_swing
from conftest import daily


@pytest.mark.parametrize("sym,expected", [("DEMO01", "Triggered"), ("DEMO04", "Triggered"), ("DEMO13", "Approaching Entry")])
def test_healthy_pullbacks_classified(demo, sym, expected):
    a = analyze_swing(daily(demo, sym), sym, bench=daily(demo, "SPY"))
    assert a.is_setup and a.status == expected
    p = a.plan
    assert p.stop < p.entry_price < p.target1 and p.validity_errors() == []
    assert p.entry_zone_low <= p.entry_zone_high


def test_breakdown_is_avoid_not_buy_the_dip(demo):
    a = analyze_swing(daily(demo, "DEMO05"), "DEMO05")
    assert a.status == "Avoid" and a.technical_state == "breaking_down"
    assert any("50-day" in r for r in a.reasons_avoid)


def test_extended_stock_is_not_a_setup(demo):
    a = analyze_swing(daily(demo, "DEMO14"), "DEMO14")
    assert not a.is_setup and a.technical_state == "extended"


def test_stop_break_invalidates_prior_plan(demo):
    a = analyze_swing(daily(demo, "DEMO16"), "DEMO16")
    assert a.status == "Invalidated" and a.plan is not None


def test_oversold_rsi_alone_is_not_a_buy(demo):
    f = daily(demo, "DEMO11")  # downtrend
    a = analyze_swing(f, "DEMO11")
    assert a.status == "Avoid" and a.plan is None


def test_not_triggered_setups_require_confirmation(demo):
    a = analyze_swing(daily(demo, "DEMO13"), "DEMO13")
    assert any("not a buy signal" in w for w in a.warnings)
    assert "close above" in a.plan.trigger_text


def test_no_lookahead_future_bars_do_not_change_past_analysis(demo):
    """Analysis at bar t must be identical whether or not later bars exist."""
    full = daily(demo, "DEMO01")
    t = len(full) - 6
    past_only = ind.add_daily_indicators(full.iloc[:t + 1][["open", "high", "low", "close", "volume"]])
    a1 = analyze_swing(past_only, "DEMO01", _check_invalidation=False)
    piv = ind.pivots(full, 3)  # pivots from the full history, filtered by confirmation time inside
    a2 = analyze_swing(full.iloc[:t + 1], "DEMO01", pivots=piv, _check_invalidation=False)
    assert a1.status == a2.status
    assert (a1.plan is None) == (a2.plan is None)
    if a1.plan:
        assert a1.plan.to_dict() == a2.plan.to_dict()
    assert [s.passed for s in a1.signals] == [s.passed for s in a2.signals]


def test_short_history_reports_missing_data():
    idx = pd.date_range("2026-01-01", periods=20, freq="B", tz="America/New_York")
    df = pd.DataFrame({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 1e6}, index=idx)
    a = analyze_swing(ind.add_daily_indicators(df), "X")
    assert a.status == "Avoid" and "history" in a.reasons_avoid[0]


def test_partial_bar_cannot_confirm_a_daily_close_trigger(demo_midday):
    f = daily(demo_midday, "DEMO01")
    assert f.attrs.get("partial_last_bar")
    a = analyze_swing(f, "DEMO01")
    assert a.status != "Triggered"
