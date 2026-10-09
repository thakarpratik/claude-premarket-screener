"""Builds the set of data adapters from the environment and reports what is (not) configured.

Demo mode uses the synthetic market only. Live mode never falls back to synthetic data: missing
keys produce explicit setup messages instead."""
from __future__ import annotations

from dataclasses import dataclass, field

from .adapters.base import (EventsProvider, FilingsProvider, MarketDataProvider, NewsProvider, NotConfigured,
                            ReferenceProvider, SentimentProvider)
from .adapters.http import PROVIDER_ERRORS, PROVIDER_STATS
from .config import ENV, EnvSettings


@dataclass
class DataHub:
    mode: str
    market: MarketDataProvider | None = None
    reference: ReferenceProvider | None = None
    news: list[NewsProvider] = field(default_factory=list)
    events: EventsProvider | None = None
    filings: FilingsProvider | None = None
    sentiment: SentimentProvider | None = None
    llm: object | None = None
    setup_messages: list[str] = field(default_factory=list)

    @property
    def synthetic(self) -> bool:
        return bool(self.market and self.market.synthetic)

    @property
    def ready(self) -> bool:
        return self.market is not None and self.reference is not None

    def status(self) -> dict:
        def nm(p):
            return getattr(p, "name", None) if p else None
        return {
            "mode": self.mode,
            "synthetic": self.synthetic,
            "ready": self.ready,
            "providers": {
                "prices": nm(self.market), "reference": nm(self.reference),
                "news": [nm(n) for n in self.news], "events": nm(self.events), "filings": nm(self.filings),
                "social_sentiment": nm(self.sentiment), "ai_news_analysis": nm(self.llm),
            },
            "delay_minutes": getattr(self.market, "delay_minutes", None),
            "setup_messages": self.setup_messages,
            "provider_stats": PROVIDER_STATS,
            "recent_errors": {k: list(v) for k, v in PROVIDER_ERRORS.items()},
        }


def build_hub(env: EnvSettings = ENV, demo_now=None) -> DataHub:
    if env.data_mode == "demo":
        from .adapters.demo import DemoMarket
        m = DemoMarket(now=demo_now)
        hub = DataHub("demo", market=m, reference=m, news=[m], events=m, filings=m, sentiment=m)
        hub.setup_messages.append("DEMO MODE: all prices, news and events are synthetic. Set DATA_MODE=live and "
                                  "configure provider keys to scan the real market.")
        _attach_llm(hub, env)
        return hub

    hub = DataHub("live")
    cache = env.cache_dir
    try:
        from .adapters.polygon import PolygonClient, PolygonMarketData, PolygonNews, PolygonReference
        pc = PolygonClient(env.polygon_api_key, env.polygon_base_url, env.polygon_requests_per_minute,
                           cache_dir=cache / "polygon")
        hub.market = PolygonMarketData(pc, env.polygon_delay_minutes)
        hub.reference = PolygonReference(pc)
        hub.news.append(PolygonNews(pc))
    except NotConfigured as e:
        hub.setup_messages.append(f"Scanner offline: {e}")
    try:
        from .adapters.finnhub import FinnhubClient, FinnhubEvents, FinnhubNews, FinnhubSentiment
        fc = FinnhubClient(env.finnhub_api_key, env.finnhub_requests_per_minute, cache_dir=cache / "finnhub")
        hub.events = FinnhubEvents(fc)
        hub.news.append(FinnhubNews(fc))
        hub.sentiment = FinnhubSentiment(fc)
    except NotConfigured as e:
        hub.setup_messages.append(f"Earnings calendar and social sentiment unavailable: {e}")
    try:
        from .adapters.sec import SecFilings
        hub.filings = SecFilings(env.sec_user_agent, cache_dir=cache / "sec")
    except NotConfigured as e:
        hub.setup_messages.append(f"SEC filing checks (dilution, insider forms) unavailable: {e}")
    _attach_llm(hub, env)
    return hub


def _attach_llm(hub: DataHub, env: EnvSettings):
    if env.anthropic_api_key:
        from .services.ai_news import ClaudeNewsAnalyst
        hub.llm = ClaudeNewsAnalyst(env.anthropic_api_key, env.anthropic_model)
    else:
        hub.setup_messages.append("AI news interpretation off (ANTHROPIC_API_KEY not set); using the documented "
                                  "rule-based news classifier.")
