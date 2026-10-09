"""Polygon.io (Massive) REST adapter: OHLCV, quotes, reference data, market cap and news.

Docs: https://polygon.io/docs/stocks. Your plan determines latency (real-time vs 15-minute delayed),
history depth and entitlements; set POLYGON_DELAY_MINUTES to match it so freshness labels stay honest."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd

from ..market_calendar import ET, UTC
from .base import (DataUnavailable, MarketDataProvider, NewsItem, NewsProvider, NotConfigured, Quote,
                   ReferenceProvider, TickerInfo)
from .http import ProviderClient, TTLCache
from .sectors import sector_etf, sector_from_sic

EXCHANGE_TYPES_OK = {"XNYS", "XNAS", "XASE", "ARCX", "BATS", "IEXG"}


def _bars_from_results(results, daily: bool) -> pd.DataFrame:
    if not results:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(results)
    idx = pd.to_datetime(df["t"], unit="ms", utc=True).dt.tz_convert(ET)
    if daily:
        idx = pd.DatetimeIndex([pd.Timestamp(datetime.combine(t.date(), time(16, 0)), tz=ET) for t in idx])
    out = pd.DataFrame({"open": df["o"].astype(float), "high": df["h"].astype(float), "low": df["l"].astype(float),
                        "close": df["c"].astype(float), "volume": df["v"].astype(float)})
    out.index = pd.DatetimeIndex(idx, name="time")
    return out.sort_index()


class PolygonClient:
    def __init__(self, api_key: str | None, base_url: str, per_minute: int, cache_dir=None, transport=None, sleep=None):
        if not api_key:
            raise NotConfigured("POLYGON_API_KEY is not set. Add it to the backend environment to enable live data.")
        kw = {"transport": transport}
        if sleep:
            kw["sleep"] = sleep
        self.http = ProviderClient("polygon", base_url, per_minute, auth_params={"apiKey": api_key},
                                   cache=TTLCache(cache_dir), **kw)


class PolygonMarketData(MarketDataProvider):
    name = "polygon"
    supports_grouped = True

    def __init__(self, client: PolygonClient, delay_minutes: int):
        self.c = client.http
        self.delay_minutes = delay_minutes

    def daily_bars(self, symbol, start, end):
        closed = end < datetime.now(ET).date()
        data = self.c.get_json(f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
                               {"adjusted": "true", "sort": "asc", "limit": 50000},
                               ttl=None if closed else 120, persist=closed)
        return _bars_from_results(data.get("results"), daily=True)

    def intraday_bars(self, symbol, day, minutes=1, extended=True):
        closed = day < datetime.now(ET).date()
        data = self.c.get_json(f"/v2/aggs/ticker/{symbol}/range/{minutes}/minute/{day}/{day}",
                               {"adjusted": "true", "sort": "asc", "limit": 50000},
                               ttl=None if closed else 30, persist=closed)
        df = _bars_from_results(data.get("results"), daily=False)
        if not extended and not df.empty:
            df = df.between_time("09:30", "15:59")
        return df

    def quote(self, symbol):
        data = self.c.get_json(f"/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}", ttl=15)
        t = data.get("ticker") or {}
        last = t.get("lastTrade") or {}
        q = t.get("lastQuote") or {}
        price = last.get("p") or (t.get("day") or {}).get("c") or (t.get("prevDay") or {}).get("c")
        ts_ns = last.get("t") or t.get("updated")
        if not price or not ts_ns:
            raise DataUnavailable(f"polygon: no snapshot price for {symbol}")
        ts = datetime.fromtimestamp(ts_ns / 1e9, tz=UTC).astimezone(ET)
        return Quote(symbol, float(price), ts, "polygon", self.delay_minutes, bid=q.get("p"), ask=q.get("P"))

    def grouped_daily(self, day):
        closed = day < datetime.now(ET).date()
        data = self.c.get_json(f"/v2/aggs/grouped/locale/us/market/stocks/{day}", {"adjusted": "true"},
                               ttl=None if closed else 300, persist=closed)
        res = data.get("results") or []
        if not res:
            return None
        df = pd.DataFrame(res).rename(columns={"T": "symbol", "o": "open", "h": "high", "l": "low", "c": "close",
                                               "v": "volume"})
        return df[["symbol", "open", "high", "low", "close", "volume"]]


class PolygonReference(ReferenceProvider):
    name = "polygon"

    def __init__(self, client: PolygonClient):
        self.c = client.http

    def list_universe(self):
        out, url, params = [], "/v3/reference/tickers", {"market": "stocks", "active": "true", "limit": 1000}
        for _ in range(20):  # ~20k tickers max
            data = self.c.get_json(url, params, ttl=24 * 3600)
            for r in data.get("results") or []:
                out.append(TickerInfo(symbol=r["ticker"], name=r.get("name", r["ticker"]),
                                      exchange=r.get("primary_exchange") or "UNKNOWN", security_type=r.get("type") or "",
                                      market_cap=None, shares_float=None, sector=None, industry=None, source="polygon",
                                      active=bool(r.get("active", True))))
            nxt = data.get("next_url")
            if not nxt:
                break
            url, params = nxt, {}
        return out

    def ticker_info(self, symbol):
        data = self.c.get_json(f"/v3/reference/tickers/{symbol}", ttl=24 * 3600)
        r = data.get("results") or {}
        if not r:
            raise DataUnavailable(f"polygon: no reference data for {symbol}")
        sector = sector_from_sic(r.get("sic_code"))
        return TickerInfo(
            symbol=symbol, name=r.get("name", symbol), exchange=r.get("primary_exchange") or "UNKNOWN",
            security_type=r.get("type") or "", market_cap=r.get("market_cap"),
            # Polygon reference data has shares outstanding, not free float; we label it as such downstream.
            shares_float=None, sector=sector, industry=r.get("sic_description"), source="polygon",
            as_of=datetime.now(ET), sector_etf=sector_etf(sector), active=bool(r.get("active", True)),
            shares_outstanding=r.get("share_class_shares_outstanding") or r.get("weighted_shares_outstanding"),
        )


    def reverse_splits(self, symbol, since: date) -> list[dict]:
        data = self.c.get_json("/v3/reference/splits", {"ticker": symbol, "execution_date.gte": since, "limit": 50},
                               ttl=24 * 3600)
        return [{"date": r.get("execution_date"), "ratio": f"{r.get('split_to')}-for-{r.get('split_from')}"}
                for r in data.get("results") or [] if (r.get("split_from") or 0) > (r.get("split_to") or 0)]


class PolygonNews(NewsProvider):
    name = "polygon"

    def __init__(self, client: PolygonClient):
        self.c = client.http

    def _items(self, params):
        data = self.c.get_json("/v2/reference/news", {"order": "desc", "sort": "published_utc", **params}, ttl=300)
        out = []
        for r in data.get("results") or []:
            ins = {i.get("ticker"): i for i in (r.get("insights") or [])}
            tick = params.get("ticker")
            i = ins.get(tick) if tick else None
            try:
                pub = datetime.fromisoformat(r["published_utc"].replace("Z", "+00:00")).astimezone(ET)
            except (KeyError, ValueError):
                continue
            out.append(NewsItem(
                id=str(r.get("id")), symbols=r.get("tickers") or [], headline=r.get("title", ""),
                publisher=(r.get("publisher") or {}).get("name", "unknown"), url=r.get("article_url", ""),
                published_at=pub, source="polygon", summary=r.get("description"),
                provider_sentiment=(i or {}).get("sentiment"), provider_sentiment_reason=(i or {}).get("sentiment_reasoning"),
            ))
        return out

    def company_news(self, symbol, since, limit=20):
        return self._items({"ticker": symbol, "published_utc.gte": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "limit": limit})

    def market_news(self, since, limit=20):
        return self._items({"published_utc.gte": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": limit})


def default_history_start(today: date, days: int = 420) -> date:
    return today - timedelta(days=days)
