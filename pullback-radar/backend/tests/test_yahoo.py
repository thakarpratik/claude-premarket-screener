from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from pullback_radar.adapters.base import DataUnavailable
from pullback_radar.adapters.yahoo import YahooData
from pullback_radar.config import EnvSettings
from pullback_radar.market_calendar import ET
from pullback_radar.providers import build_hub

NOW = datetime(2026, 10, 8, 11, 0, tzinfo=ET)


class FakeTicker:
    def __init__(self, sym, calls):
        self.sym, self.calls = sym, calls
        self.splits = pd.Series([0.05], index=pd.DatetimeIndex([pd.Timestamp("2026-06-01", tz=ET)]))
        self.info = {"longName": f"{sym} Corp", "exchange": "NMS", "quoteType": "EQUITY", "marketCap": 5e9,
                     "floatShares": 9e7, "sharesOutstanding": 1e8, "sector": "Financial Services",
                     "industry": "Banks"}

    def history(self, start=None, end=None, interval="1d", **kw):
        self.calls.append((self.sym, interval))
        if interval == "1d":
            idx = pd.date_range("2025-08-01", "2026-10-08", freq="B", tz=ET)
        else:
            idx = pd.date_range(datetime(2026, 10, 8, 9, 30), periods=60, freq="1min", tz=ET)
        n = len(idx)
        base = [100 + 0.1 * x for x in range(n)]
        return pd.DataFrame({"Open": base, "High": [x + 1 for x in base], "Low": [x - 1 for x in base],
                             "Close": [x + 0.5 for x in base], "Volume": [1e6] * n}, index=idx)

    def get_news(self, count=10):
        return [{"id": "a1", "content": {"title": "Corp beats estimates", "pubDate": "2026-10-07T13:00:00Z",
                                         "provider": {"displayName": "Reuters"},
                                         "canonicalUrl": {"url": "https://example.com/a1"}, "contentType": "STORY"}},
                {"title": "Old format item", "publisher": "Benzinga", "link": "https://example.com/b",
                 "providerPublishTime": int(datetime(2026, 10, 6, tzinfo=ET).timestamp()), "uuid": "b"}]

    def get_earnings_dates(self, limit=12):
        return pd.DataFrame({"EPS Estimate": [1.0]}, index=pd.DatetimeIndex([pd.Timestamp("2026-10-20 16:05", tz=ET)]))


class FakeYF:
    def __init__(self):
        self.calls = []

    def Ticker(self, sym):  # noqa: N802 — mirrors yfinance
        return FakeTicker(sym, self.calls)


def make():
    fy = FakeYF()
    return YahooData(15, 1000, ["ABC"], yf_module=fy, now_fn=lambda: NOW), fy


def test_daily_bars_and_partial_flag():
    y, _ = make()
    d = y.daily_bars("ABC", date(2026, 9, 1), date(2026, 10, 8))
    assert list(d.columns) == ["open", "high", "low", "close", "volume"]
    assert d.index[-1].hour == 16 and d.attrs.get("partial_last_bar")  # 11:00 ET: today's bar still forming


def test_intraday_quote_and_caching():
    y, fy = make()
    b = y.intraday_bars("ABC", date(2026, 10, 8), 5, extended=False)
    assert len(b) == 12
    q = y.quote("ABC")
    assert q.delay_minutes == 15 and q.timestamp <= NOW and q.source.startswith("yahoo")
    assert sum(1 for c in fy.calls if c[1] == "1m") == 1  # second request served from cache
    with pytest.raises(DataUnavailable):
        y.intraday_bars("ABC", date(2026, 8, 1))


def test_reference_mapping_and_splits():
    y, _ = make()
    i = y.ticker_info("ABC")
    assert (i.exchange, i.security_type, i.sector, i.sector_etf) == ("XNAS", "CS", "Financials", "XLF")
    assert y.reverse_splits("ABC", date(2025, 10, 1))[0]["ratio"] == "1-for-20"
    assert [t.symbol for t in y.list_universe()] == ["ABC"]


def test_news_both_formats_and_earnings():
    y, _ = make()
    n = y.company_news("ABC", datetime(2026, 10, 1, tzinfo=ET))
    assert [x.publisher for x in n] == ["Reuters", "Benzinga"] and n[0].url == "https://example.com/a1"
    e = y.earnings(["ABC"], date(2026, 10, 1), date(2026, 11, 1))
    assert e[0].date == date(2026, 10, 20) and e[0].time_of_day == "amc"


def test_errors_become_data_unavailable():
    class Boom(FakeYF):
        def Ticker(self, sym):  # noqa: N802
            raise RuntimeError("rate limited")
    y = YahooData(15, 1000, ["ABC"], yf_module=Boom(), now_fn=lambda: NOW)
    with pytest.raises(DataUnavailable):
        y.daily_bars("ABC", date(2026, 9, 1), date(2026, 10, 8))


def test_live_mode_without_keys_uses_yahoo_not_demo():
    hub = build_hub(EnvSettings(data_mode="live"))
    assert hub.ready and not hub.synthetic and hub.market.name.startswith("yahoo")
    assert hub.events is hub.market  # earnings dates from Yahoo when Finnhub is not configured
    assert any("Yahoo" in m for m in hub.setup_messages)


def test_full_scan_runs_on_yahoo_adapter():
    from pullback_radar.providers import DataHub
    from pullback_radar.services.scan import run_scan
    from pullback_radar.config import ScanSettings
    y, _ = make()
    hub = DataHub("live", market=y, reference=y, news=[y], events=y)
    r = run_scan(hub, ScanSettings(), EnvSettings(data_mode="live"), now=NOW)
    assert r["ok"] and not r["synthetic"] and r["universe_size"] == 1
    card = r["swing"]["all"]["ABC"]
    assert card["freshness"]["status"] in ("delayed", "stale") and card["sources"]["prices"].startswith("yahoo")
