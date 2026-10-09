from datetime import date, datetime, timedelta

from pullback_radar.adapters.base import Event, NewsItem
from pullback_radar.config import ScanSettings
from pullback_radar.market_calendar import ET
from pullback_radar.services import events, manipulation, news
from conftest import daily


def test_pump_scores_high_with_flags(demo):
    f = daily(demo, "DEMO06")
    info = demo.ticker_info("DEMO06")
    r = manipulation.assess(f, info, verified_catalyst=False, promotional_news=1, social=demo.social("DEMO06"),
                            filings=demo.recent_filings("DEMO06", date(2026, 1, 1)),
                            reverse_splits=demo.reverse_splits("DEMO06", date(2025, 10, 1)))
    keys = {x["key"] for x in r["flags"]}
    assert r["level"] == "high" and r["score"] >= 55
    assert {"parabolic", "volume_no_news", "reverse_split", "dilution", "social_spike"} <= keys
    assert "Not evidence" in r["disclaimer"]


def test_large_cap_capped_below_elevated(demo):
    f = daily(demo, "DEMO06")
    info = demo.ticker_info("DEMO06")
    info.market_cap = 50e9
    r = manipulation.assess(f, info, verified_catalyst=False, reverse_splits=[], filings=[], social=None)
    assert r["score"] < manipulation.ELEVATED


def test_healthy_large_cap_low_risk(demo):
    r = manipulation.assess(daily(demo, "DEMO01"), demo.ticker_info("DEMO01"), verified_catalyst=True, filings=[],
                            reverse_splits=[], social=demo.social("DEMO01"))
    assert r["level"] == "low"


def test_missing_inputs_are_reported_not_assumed(demo):
    r = manipulation.assess(daily(demo, "DEMO01"), demo.ticker_info("DEMO01"), verified_catalyst=True)
    assert {"SEC filings", "split history", "social-media activity"} <= set(r["unknown"])


def _item(headline, publisher="Reuters", stype="news", sentiment=None):
    return NewsItem("1", ["X"], headline, publisher, "https://example.com/a", datetime(2026, 10, 1, 9, tzinfo=ET), "t",
                    provider_sentiment=sentiment, source_type=stype)


def test_news_verification_tiers():
    assert news.verification(_item("Shares could soar 1,000%! Next 100x"))[0] == "promotional"
    assert news.verification(_item("Co reports results", stype="press_release"))[0] == "verified"
    assert news.verification(_item("3 stocks to buy", publisher="The Motley Fool"))[0] == "opinion"
    assert news.verification(_item("Rumor on forum", stype="social"))[0] == "unverified"
    assert news.verification(_item("Company beats estimates"))[0] == "established"


def test_news_category_sentiment_and_score():
    a = news.analyse(_item("Company raises full-year guidance after record quarter"))
    assert a["category"] == "guidance" and a["impact"] == "high" and a["sentiment"] == "positive"
    b = news.analyse(_item("Company announces $50M share offering", stype="press_release"))
    assert b["category"] == "offering" and b["sentiment"] == "negative"
    s, _ = news.news_score([a])
    assert s > 60
    promo = news.analyse(_item("XYZ to the moon, could soar 1000%", stype="social"))
    assert news.news_score([promo])[0] == 50  # promotional items carry no weight
    assert promo["impact"] != "high"


def test_provider_sentiment_preferred():
    a = news.analyse(_item("Company cuts outlook", sentiment="positive"))
    assert a["sentiment"] == "positive" and a["sentiment_method"].startswith("provider")


def test_priced_in_measurement(demo):
    f = daily(demo, "DEMO01")
    t = f.index[-10]
    r = news.priced_in(t.to_pydatetime(), "positive", f, 1.0)
    assert r["status"] in ("largely_reflected", "not_clearly_reflected", "contrary_move")
    assert news.priced_in(t.to_pydatetime(), "positive", None, 1.0)["status"] == "not_measurable"


def test_earnings_blackout_blocks_swing():
    s = ScanSettings()
    ev = [Event("X", "earnings", date(2026, 10, 12), "Earnings", "t", "amc", "high")]
    r = events.event_risk("X", "swing", date(2026, 10, 8), ev, s, True)
    assert r["blocking"] and r["next_earnings"]["trading_days_away"] == 2
    r2 = events.event_risk("X", "swing", date(2026, 10, 8), ev, ScanSettings(exclude_before_earnings=False), True)
    assert not r2["blocking"]
    r3 = events.event_risk("X", "swing", date(2026, 10, 8), [], s, False)
    assert any("unavailable" in n for n in r3["notes"])


def test_event_driven_decline_detected(demo):
    f = daily(demo, "DEMO07")
    evs = demo.earnings(["DEMO07"], date(2026, 9, 1), date(2026, 11, 1))
    nws = [news.analyse(n) for n in demo.company_news("DEMO07", demo.now() - timedelta(days=14))]
    d = events.event_driven_decline(f, evs, nws, "DEMO07")
    assert d and "Not treated as an ordinary pullback" in d["message"]
    assert events.event_driven_decline(daily(demo, "DEMO01"), [], [], "DEMO01") is None


def test_fomc_file_loads():
    assert any(e.kind == "fomc" for e in events.load_fomc())
