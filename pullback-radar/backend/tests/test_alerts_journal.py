from datetime import datetime, timezone
from types import SimpleNamespace

from pullback_radar.db import Alert, User, WatchlistItem, init_db, make_engine
from pullback_radar.market_calendar import ET
from pullback_radar.services import alerts, journal


def card(price, status="Approaching Entry", news=None):
    return {"symbol": "X", "price": price, "price_time": "2026-10-08T15:00:00-04:00", "status": status,
            "bar_as_of": "b", "plan": {"entry_zone_low": 98, "entry_zone_high": 101, "trigger_price": 102,
                                       "trigger_text": "close above 102", "stop": 95, "target1": 110,
                                       "target1_text": "high", "target2": None, "target2_text": None},
            "news": news or [], "metrics": {"rel_volume": 1.0, "day_change_pct": 0.5, "atr_pct": 2.0},
            "manipulation": {"level": "low", "score": 5, "flags": []}, "events": {"next_earnings": None}}


def scan(c):
    return {"ok": True, "synthetic": True, "swing": {"all": {"X": c}}}


def test_alerts_fire_once_and_rearm():
    Session = init_db(make_engine("sqlite://"))
    with Session() as db:
        u = User(email="a@b.c", password_hash="x")
        db.add(u)
        db.flush()
        db.add(WatchlistItem(user_id=u.id, symbol="X", style="swing"))
        db.flush()
        first = alerts.evaluate_user(db, u.id, scan(card(100)))
        assert [a.kind for a in first] == ["entry_zone"]
        assert "no order has been placed" in first[0].message and first[0].link == "/research/X?style=swing"
        assert alerts.evaluate_user(db, u.id, scan(card(100.5))) == []  # unchanged condition: no repeat
        assert alerts.evaluate_user(db, u.id, scan(card(104))) == []  # left the zone
        again = alerts.evaluate_user(db, u.id, scan(card(99)))
        assert [a.kind for a in again] == ["entry_zone"]  # re-entered: fires again
        stop = alerts.evaluate_user(db, u.id, scan(card(94, status="Invalidated")))
        assert "invalidation" in [a.kind for a in stop]
        n = {"id": "n1", "impact": "high", "verification": "verified", "sentiment": "negative", "headline": "h",
             "publisher": "p", "published_at": "2026-10-08T10:00"}
        assert [a.kind for a in alerts.evaluate_user(db, u.id, scan(card(94, "Invalidated", [n])))] == ["news"]
        assert db.query(Alert).count() == 4


def test_journal_stats_keep_modes_separate():
    def tr(mode, entry, exit_, stop=None):
        return SimpleNamespace(mode=mode, status="closed", entry_price=entry, exit_price=exit_, quantity=10, fees=0,
                               stop=stop, style="swing", strategy="pb", exit_time=datetime(2026, 10, 8, tzinfo=timezone.utc),
                               entry_time=datetime(2026, 10, 1, tzinfo=timezone.utc))
    trades = [tr("paper", 10, 12, 9), tr("paper", 10, 9, 9), tr("actual", 10, 11)]
    s = journal.journal_stats(trades)
    assert s["paper"]["overall"]["trades"] == 2 and s["actual"]["overall"]["trades"] == 1
    assert s["paper"]["overall"]["profit_factor"] == 2.0 and s["paper"]["overall"]["avg_r"] == 0.5
    assert s["paper"]["overall"]["max_drawdown"] == 10


def test_paper_trade_updates():
    t = SimpleNamespace(status="open", mode="paper", stop=95.0, target1=110.0, target2=120.0, entry_price=100.0,
                        style="swing", setup_snapshot={}, exit_price=None, exit_time=None, exit_reason=None)
    now = datetime(2026, 10, 8, 12, tzinfo=ET)
    assert "breakeven" in journal.update_paper_trade(t, 111, now, now) and t.stop == 100
    assert journal.update_paper_trade(t, 99, now, now).startswith("Stopped") and t.status == "closed"
