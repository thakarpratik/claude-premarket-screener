"""Finnhub adapter: earnings calendar, company news and (premium) social sentiment.

Docs: https://finnhub.io/docs/api. Endpoints your plan does not include return 403 and are
reported as unavailable rather than guessed."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..market_calendar import ET, UTC
from .base import (DataUnavailable, Event, EventsProvider, NewsItem, NewsProvider, NotConfigured, SentimentProvider,
                   SocialStats)
from .http import ProviderClient, TTLCache


class FinnhubClient:
    def __init__(self, api_key: str | None, per_minute: int, cache_dir=None, transport=None, sleep=None):
        if not api_key:
            raise NotConfigured("FINNHUB_API_KEY is not set (earnings calendar / news / social sentiment).")
        kw = {"transport": transport}
        if sleep:
            kw["sleep"] = sleep
        self.http = ProviderClient("finnhub", "https://finnhub.io/api/v1", per_minute, auth_params={"token": api_key},
                                   cache=TTLCache(cache_dir), **kw)


class FinnhubEvents(EventsProvider):
    name = "finnhub"

    def __init__(self, client: FinnhubClient):
        self.c = client.http

    def earnings(self, symbols, start, end):
        out = []
        want = set(symbols)
        if len(symbols) <= 5:
            rows = []
            for s in symbols:
                rows += self.c.get_json("/calendar/earnings", {"from": start, "to": end, "symbol": s},
                                        ttl=6 * 3600).get("earningsCalendar") or []
        else:
            rows = self.c.get_json("/calendar/earnings", {"from": start, "to": end},
                                   ttl=6 * 3600).get("earningsCalendar") or []
        for r in rows:
            if r.get("symbol") not in want:
                continue
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                continue
            hour = r.get("hour") or None
            est = r.get("epsEstimate")
            desc = "Earnings report" + (f" (EPS est. {est})" if est is not None else "")
            out.append(Event(symbol=r["symbol"], kind="earnings", date=d, description=desc, source="finnhub",
                             time_of_day=hour, importance="high",
                             url=f"https://finnhub.io/docs/api/earnings-calendar"))
        return out

    def economic(self, start, end):
        try:
            rows = self.c.get_json("/calendar/economic", {"from": start, "to": end}, ttl=6 * 3600)
        except NotConfigured:
            return []  # premium endpoint
        out = []
        for r in (rows.get("economicCalendar") or []):
            if (r.get("country") or "").upper() != "US" or r.get("impact") not in ("high", "medium"):
                continue
            try:
                d = datetime.fromisoformat(r["time"].replace(" ", "T")).date()
            except (KeyError, ValueError):
                continue
            out.append(Event(symbol=None, kind="economic", date=d, description=r.get("event", "Economic release"),
                             source="finnhub", importance=r.get("impact", "medium")))
        return out


class FinnhubNews(NewsProvider):
    name = "finnhub"

    def __init__(self, client: FinnhubClient):
        self.c = client.http

    def company_news(self, symbol, since, limit=20):
        rows = self.c.get_json("/company-news", {"symbol": symbol, "from": since.date(),
                                                 "to": datetime.now(ET).date()}, ttl=300)
        out = []
        for r in rows[: limit * 2] if isinstance(rows, list) else []:
            try:
                pub = datetime.fromtimestamp(int(r["datetime"]), tz=UTC).astimezone(ET)
            except (KeyError, ValueError, TypeError):
                continue
            if pub < since:
                continue
            out.append(NewsItem(id=f"fh-{r.get('id')}", symbols=[s for s in (r.get("related") or symbol).split(",") if s],
                                headline=r.get("headline", ""), publisher=r.get("source", "unknown"),
                                url=r.get("url", ""), published_at=pub, source="finnhub", summary=r.get("summary")))
        return out[:limit]

    def market_news(self, since, limit=20):
        rows = self.c.get_json("/news", {"category": "general"}, ttl=300)
        out = []
        for r in rows if isinstance(rows, list) else []:
            try:
                pub = datetime.fromtimestamp(int(r["datetime"]), tz=UTC).astimezone(ET)
            except (KeyError, ValueError, TypeError):
                continue
            if pub >= since:
                out.append(NewsItem(id=f"fh-{r.get('id')}", symbols=[], headline=r.get("headline", ""),
                                    publisher=r.get("source", "unknown"), url=r.get("url", ""), published_at=pub,
                                    source="finnhub", summary=r.get("summary")))
        return out[:limit]


class FinnhubSentiment(SentimentProvider):
    """Social-media mention counts (Reddit/X) via Finnhub's licensed aggregate. Premium on most plans."""
    name = "finnhub"

    def __init__(self, client: FinnhubClient):
        self.c = client.http

    def social(self, symbol):
        start = (datetime.now(ET) - timedelta(days=30)).date()
        try:
            data = self.c.get_json("/stock/social-sentiment", {"symbol": symbol, "from": start}, ttl=1800)
        except (NotConfigured, DataUnavailable):
            return None
        rows = (data.get("reddit") or []) + (data.get("twitter") or [])
        if not rows:
            return None
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault((r.get("atTime") or "")[:10], []).append(r)
        days = sorted(k for k in by_day if k)
        if not days:
            return None
        counts = [sum(x.get("mention", 0) for x in by_day[d]) for d in days]
        today = by_day[days[-1]]
        pos = sum(x.get("positiveMention", 0) for x in today)
        tot = sum(x.get("mention", 0) for x in today)
        prior = counts[:-1]
        return SocialStats(symbol=symbol, mentions_today=counts[-1],
                           mentions_avg=(sum(prior) / len(prior)) if prior else None,
                           positive_share=(pos / tot) if tot else None, source="finnhub",
                           as_of=datetime.now(ET))
