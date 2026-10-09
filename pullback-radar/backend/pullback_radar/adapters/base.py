"""Provider-neutral data contracts. Each data category has its own replaceable adapter interface.

Every payload carries its source and timestamp so the UI can show provenance and freshness."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

import pandas as pd


class DataUnavailable(Exception):
    """Raised when a provider cannot supply data. Callers must surface this, never paper over it."""


class NotConfigured(DataUnavailable):
    """A required API key or setting is missing."""


@dataclass
class Quote:
    symbol: str
    price: float
    timestamp: datetime
    source: str
    delay_minutes: int
    bid: float | None = None
    ask: float | None = None

    @property
    def spread_pct(self) -> float | None:
        if self.bid and self.ask and self.ask >= self.bid > 0:
            return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 100
        return None


@dataclass
class TickerInfo:
    symbol: str
    name: str
    exchange: str  # 'XNYS', 'XNAS', 'ARCX', 'BATS', 'OTC', ...
    security_type: str  # 'CS' common stock, 'ETF', 'PFD', 'WARRANT', 'ADRC', ...
    market_cap: float | None
    shares_float: float | None
    sector: str | None
    industry: str | None
    source: str
    as_of: datetime | None = None
    sector_etf: str | None = None
    active: bool = True
    shares_outstanding: float | None = None

    def to_dict(self):
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat() if self.as_of else None
        return d


@dataclass
class NewsItem:
    id: str
    symbols: list[str]
    headline: str
    publisher: str
    url: str
    published_at: datetime
    source: str
    summary: str | None = None
    provider_sentiment: str | None = None  # sentiment the provider itself assigned, if any
    provider_sentiment_reason: str | None = None
    source_type: str = "news"  # news | press_release | filing | analyst | opinion | social


@dataclass
class Event:
    symbol: str | None  # None = market-wide (macro)
    kind: str  # earnings | ex_dividend | economic | fomc | fda | court | corporate
    date: date
    description: str
    source: str
    time_of_day: str | None = None  # 'bmo' | 'amc' | 'dmh' | HH:MM
    importance: str = "medium"
    url: str | None = None

    def to_dict(self):
        d = asdict(self)
        d["date"] = self.date.isoformat()
        return d


@dataclass
class Filing:
    symbol: str
    form: str
    filed: date
    url: str
    description: str = ""


@dataclass
class SocialStats:
    symbol: str
    mentions_today: int | None
    mentions_avg: float | None
    positive_share: float | None
    source: str
    as_of: datetime | None = None
    extra: dict = field(default_factory=dict)


class MarketDataProvider(ABC):
    name: str = "base"
    synthetic: bool = False
    delay_minutes: int = 0
    supports_grouped: bool = False  # can list every symbol's bars for a session in one call

    @abstractmethod
    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Adjusted daily OHLCV, index = session date at 16:00 ET (tz-aware)."""

    @abstractmethod
    def intraday_bars(self, symbol: str, day: date, minutes: int = 1, extended: bool = True) -> pd.DataFrame:
        """Intraday OHLCV for one session, tz-aware ET index. Includes pre-market when extended=True."""

    @abstractmethod
    def quote(self, symbol: str) -> Quote:
        ...

    def grouped_daily(self, day: date) -> pd.DataFrame | None:
        """Optional: one row per symbol for a whole session (used for cheap universe filtering)."""
        return None


class ReferenceProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_universe(self) -> list[TickerInfo]:
        """Active listed securities (any type: the universe filter excludes non-common equity)."""

    @abstractmethod
    def ticker_info(self, symbol: str) -> TickerInfo:
        ...


class NewsProvider(ABC):
    name: str = "base"

    @abstractmethod
    def company_news(self, symbol: str, since: datetime, limit: int = 20) -> list[NewsItem]:
        ...

    def market_news(self, since: datetime, limit: int = 20) -> list[NewsItem]:
        return []


class EventsProvider(ABC):
    name: str = "base"

    @abstractmethod
    def earnings(self, symbols: list[str], start: date, end: date) -> list[Event]:
        ...

    def economic(self, start: date, end: date) -> list[Event]:
        return []


class FilingsProvider(ABC):
    name: str = "base"

    @abstractmethod
    def recent_filings(self, symbol: str, since: date) -> list[Filing]:
        ...


class SentimentProvider(ABC):
    name: str = "base"

    @abstractmethod
    def social(self, symbol: str) -> SocialStats | None:
        ...
