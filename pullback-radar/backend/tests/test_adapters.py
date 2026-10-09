import json
from datetime import date, datetime

import httpx
import pytest

from pullback_radar.adapters.base import DataUnavailable, NotConfigured
from pullback_radar.adapters.finnhub import FinnhubClient, FinnhubEvents
from pullback_radar.adapters.http import ProviderClient, RateLimiter
from pullback_radar.adapters.polygon import PolygonClient, PolygonMarketData, PolygonNews, PolygonReference
from pullback_radar.adapters.sec import SecFilings
from pullback_radar.config import EnvSettings
from pullback_radar.market_calendar import ET
from pullback_radar.providers import build_hub


def transport(routes):
    def handler(req: httpx.Request):
        for frag, resp in routes.items():
            if frag in str(req.url):
                status, body = resp if isinstance(resp, tuple) else (200, resp)
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


def test_polygon_requires_key():
    with pytest.raises(NotConfigured):
        PolygonClient(None, "https://api.polygon.io", 5)


def test_polygon_parsing():
    t0 = int(datetime(2026, 10, 7, tzinfo=ET).timestamp() * 1000)
    routes = {
        "/range/1/day/": {"results": [{"t": t0, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1000}]},
        "/v3/reference/tickers/ABC": {"results": {"ticker": "ABC", "name": "ABC Inc", "market_cap": 5e9,
                                                  "primary_exchange": "XNAS", "type": "CS", "sic_code": "7372",
                                                  "share_class_shares_outstanding": 1e8, "active": True}},
        "/v2/reference/news": {"results": [{"id": "n1", "title": "ABC beats estimates", "publisher": {"name": "Reuters"},
                                            "published_utc": "2026-10-07T13:00:00Z", "article_url": "https://x/1",
                                            "tickers": ["ABC"], "insights": [{"ticker": "ABC", "sentiment": "positive",
                                                                              "sentiment_reasoning": "beat"}]}]},
        "/v2/snapshot": {"ticker": {"lastTrade": {"p": 10.6, "t": t0 * 1_000_000}, "lastQuote": {"p": 10.59, "P": 10.61}}},
    }
    pc = PolygonClient("k", "https://api.polygon.io", 100, transport=transport(routes))
    md = PolygonMarketData(pc, 15)
    d = md.daily_bars("ABC", date(2026, 10, 1), date(2026, 10, 7))
    assert d.index[0].hour == 16 and d.index[0].date() == date(2026, 10, 7) and d["close"].iloc[0] == 10.5
    info = PolygonReference(pc).ticker_info("ABC")
    assert info.sector == "Technology" and info.shares_float is None and info.shares_outstanding == 1e8
    n = PolygonNews(pc).company_news("ABC", datetime(2026, 10, 1, tzinfo=ET))
    assert n[0].provider_sentiment == "positive" and n[0].publisher == "Reuters"
    q = md.quote("ABC")
    assert q.price == 10.6 and q.delay_minutes == 15 and q.spread_pct < 0.5


def test_auth_errors_surface_as_not_configured():
    pc = PolygonClient("bad", "https://api.polygon.io", 100, transport=transport({"/range/": (403, {})}))
    with pytest.raises(NotConfigured):
        PolygonMarketData(pc, 15).daily_bars("ABC", date(2026, 1, 1), date(2026, 1, 2))


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})
    c = ProviderClient("t", "https://x", 100, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert c.get_json("/a", ttl=0) == {"ok": 1} and calls["n"] == 3


def test_gives_up_after_retries():
    c = ProviderClient("t2", "https://x", 100, transport=httpx.MockTransport(lambda r: httpx.Response(500)),
                       sleep=lambda s: None, max_retries=2)
    with pytest.raises(DataUnavailable):
        c.get_json("/a", ttl=0)


def test_cache_hit_avoids_second_request():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json={"v": calls["n"]})
    c = ProviderClient("t3", "https://x", 100, transport=httpx.MockTransport(handler))
    assert c.get_json("/a", ttl=60) == c.get_json("/a", ttl=60) and calls["n"] == 1


def test_rate_limiter_waits():
    t = {"now": 0.0}
    slept = []
    rl = RateLimiter(2, sleep=lambda s: (slept.append(s), t.__setitem__("now", t["now"] + s)), clock=lambda: t["now"])
    rl.acquire(); rl.acquire(); rl.acquire()
    assert slept and slept[0] == pytest.approx(60.01)


def test_finnhub_earnings():
    routes = {"/calendar/earnings": {"earningsCalendar": [{"date": "2026-10-20", "symbol": "ABC", "hour": "amc",
                                                           "epsEstimate": 1.2}]}}
    ev = FinnhubEvents(FinnhubClient("k", 100, transport=transport(routes))).earnings(["ABC"], date(2026, 10, 1),
                                                                                       date(2026, 11, 1))
    assert ev[0].date == date(2026, 10, 20) and ev[0].time_of_day == "amc"


def test_sec_filings():
    routes = {"company_tickers.json": {"0": {"cik_str": 123, "ticker": "ABC", "title": "ABC"}},
              "/submissions/CIK0000000123.json": {"filings": {"recent": {
                  "form": ["424B5", "10-Q"], "filingDate": ["2026-09-01", "2026-08-01"],
                  "accessionNumber": ["0001-26-1", "0001-26-2"], "primaryDocument": ["a.htm", "b.htm"],
                  "primaryDocDescription": ["", ""]}}}}
    s = SecFilings("Test test@example.com", transport=transport(routes))
    f = s.recent_filings("ABC", date(2026, 8, 15))
    assert [x.form for x in f] == ["424B5"] and f[0].url.endswith("/123/0001261/a.htm")
    with pytest.raises(NotConfigured):
        SecFilings(None)


def test_live_mode_without_keys_never_falls_back_to_demo():
    hub = build_hub(EnvSettings(data_mode="live"))
    assert not hub.ready and not hub.synthetic and hub.market is None
    assert any("POLYGON_API_KEY" in m for m in hub.setup_messages)
