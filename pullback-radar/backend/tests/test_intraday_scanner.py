from datetime import datetime, time

from pullback_radar.adapters.demo import DemoMarket
from pullback_radar.market_calendar import ET
from pullback_radar.scanner.intraday import analyze_intraday, expected_volume_fraction
from conftest import daily


def run(now, sym):
    m = DemoMarket(now=now)
    f = daily(m, sym)
    prior = f[f.index.date < now.date()]
    b = m.intraday_bars(sym, now.date(), 1)
    b = b[b.index.time < time(16, 0)]
    return analyze_intraday(b, sym, prior, spy1=m.intraday_bars("SPY", now.date(), 1))


def test_opening_range_must_form_first():
    a = run(datetime(2026, 10, 8, 9, 40, tzinfo=ET), "DEMO01")
    assert a.technical_state == "opening_range_forming" and a.plan is None


def test_premarket_reports_gap_without_setup():
    a = run(datetime(2026, 10, 8, 8, 0, tzinfo=ET), "DEMO06")
    assert a.technical_state == "premarket" and a.metrics["premarket_available"]
    assert a.metrics["premarket_gap_pct"] > 3


def test_vwap_reclaim_detected():
    a = run(datetime(2026, 10, 8, 11, 15, tzinfo=ET), "DEMO01")
    assert a.setup_type == "VWAP reclaim" and a.plan and a.plan.stop < a.plan.entry_price < a.plan.target1


def test_fade_below_vwap_is_breakdown():
    a = run(datetime(2026, 10, 8, 11, 15, tzinfo=ET), "DEMO05")
    assert a.technical_state == "breaking_down" and a.status == "Avoid"


def test_volume_curve_monotonic():
    xs = [expected_volume_fraction(m) for m in range(570, 961, 15)]
    assert xs == sorted(xs) and xs[0] == 0 and xs[-1] == 1


def test_intraday_no_lookahead():
    now = datetime(2026, 10, 8, 11, 15, tzinfo=ET)
    later = DemoMarket(now=datetime(2026, 10, 8, 15, 0, tzinfo=ET))
    f = daily(later, "DEMO01")
    prior = f[f.index.date < now.date()]
    b_full = later.intraday_bars("DEMO01", now.date(), 1)
    cut = b_full[b_full.index < now]
    a_late = analyze_intraday(cut, "DEMO01", prior, _check_invalidation=False)
    a_now = analyze_intraday(DemoMarket(now=now).intraday_bars("DEMO01", now.date(), 1), "DEMO01", prior,
                             _check_invalidation=False)
    assert a_late.status == a_now.status
    assert (a_late.plan.to_dict() if a_late.plan else None) == (a_now.plan.to_dict() if a_now.plan else None)
