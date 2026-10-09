from datetime import date, datetime, timedelta

from pullback_radar.market_calendar import (ET, early_closes, freshness, holidays, is_trading_day,
                                            last_completed_session, session_state, trading_days_between)


def test_2026_holidays_rule_based():
    h = holidays(2026)
    for d in ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03",
              "2026-09-07", "2026-11-26", "2026-12-25"]:
        assert date.fromisoformat(d) in h, d
    assert date(2026, 11, 27) in early_closes(2026) and date(2026, 12, 24) in early_closes(2026)


def test_saturday_new_year_not_observed_on_friday():
    assert date(2021, 12, 31) not in holidays(2021) and date(2022, 1, 1) not in holidays(2022)


def test_special_closure_2025():
    assert not is_trading_day(date(2025, 1, 9))


def test_session_states():
    assert session_state(datetime(2026, 10, 8, 8, 0, tzinfo=ET)) == "pre"
    assert session_state(datetime(2026, 10, 8, 9, 30, tzinfo=ET)) == "open"
    assert session_state(datetime(2026, 10, 8, 16, 0, tzinfo=ET)) == "after"
    assert session_state(datetime(2026, 10, 10, 12, 0, tzinfo=ET)) == "closed"  # Saturday
    assert session_state(datetime(2026, 11, 27, 13, 30, tzinfo=ET)) == "after"  # early close


def test_last_completed_session_and_counts():
    assert last_completed_session(datetime(2026, 10, 12, 10, 0, tzinfo=ET)) == date(2026, 10, 9)
    assert trading_days_between(date(2026, 10, 8), date(2026, 10, 12)) == 2


def test_freshness_labels():
    now = datetime(2026, 10, 8, 11, 0, tzinfo=ET)
    assert freshness(None, 15, now=now).status == "unavailable"
    assert freshness(now - timedelta(minutes=16), 15, now=now).status == "delayed"
    assert freshness(now - timedelta(minutes=1), 0, now=now).status == "real-time"
    assert freshness(now - timedelta(minutes=90), 15, now=now).status == "stale"
    assert freshness(now, 0, synthetic=True, now=now).status == "synthetic"
    closed = datetime(2026, 10, 10, 12, 0, tzinfo=ET)
    f = freshness(datetime(2026, 10, 9, 16, 0, tzinfo=ET), 15, now=closed)
    assert f.status == "end-of-day" and "may no longer be valid" in f.note
    assert freshness(datetime(2026, 10, 7, 16, 0, tzinfo=ET), 15, now=closed).status == "stale"
