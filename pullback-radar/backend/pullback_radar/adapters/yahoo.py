"""Yahoo Finance adapter via the `yfinance` library — free, no API key, for PERSONAL use.

Yahoo has no official public API: data access is unofficial, may be rate-limited or change without notice,
and its terms do not permit redistribution. Use it for your own research only; use a licensed provider
(e.g. Polygon) for anything shared with others. Prices are labelled with the timestamp of the last bar
actually received, and the default delay assumption (YAHOO_DELAY_MINUTES=15) is conservative.

Yahoo has no listing of every U.S. stock, so the universe comes from UNIVERSE_SYMBOLS or the default
list below (the same liquid names Move Radar scans)."""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta

import pandas as pd

from ..market_calendar import ET, UTC, now_et
from .base import (DataUnavailable, Event, EventsProvider, MarketDataProvider, NewsItem, NewsProvider, Quote,
                   ReferenceProvider, TickerInfo)
from .http import PROVIDER_STATS, RateLimiter, TTLCache, record_error
from .sectors import sector_etf

log = logging.getLogger("pullback_radar.yahoo")

DEFAULT_UNIVERSE = """
AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD NFLX ORCL CRM ADBE INTC QCOM MU TXN AMAT LRCX KLAC
MRVL ARM SMCI PLTR SNOW CRWD PANW ZS NET DDOG SHOP UBER ABNB COIN HOOD XYZ PYPL SOFI AFRM RBLX
RIVN LCID NIO F GM JPM BAC WFC C GS MS SCHW V MA AXP BRK-B UNH LLY JNJ PFE MRK ABBV MRNA BMY
AMGN GILD VRTX REGN ISRG XOM CVX OXY COP SLB WMT COST TGT HD LOW NKE SBUX MCD DIS BA CAT DE GE
LMT RTX UPS FDX DAL UAL CCL MARA RIOT MSTR SMR OKLO IONQ RGTI SOUN CVNA DKNG ROKU SNAP PINS TTD
ENPH FSLR CELH ON WDC STX DELL HPE ANET VRT APP
""".split()

EXCHANGES = {"NMS": "XNAS", "NGM": "XNAS", "NCM": "XNAS", "NAS": "XNAS", "NYQ": "XNYS", "NYS": "XNYS",
             "ASE": "XASE", "PCX": "ARCX", "BTS": "BATS", "PNK": "OTC", "OTC": "OTC", "OQB": "OTC", "OQX": "OTC"}
TYPES = {"EQUITY": "CS", "ETF": "ETF", "MUTUALFUND": "FUND", "INDEX": "INDEX"}
SECTORS = {"Technology": "Technology", "Financial Services": "Financials", "Healthcare": "Health Care",
           "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
           "Energy": "Energy", "Industrials": "Industrials", "Basic Materials": "Materials",
           "Utilities": "Utilities", "Real Estate": "Real Estate", "Communication Services": "Communication Services"}
COLS = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}


def _clean_bars(df: pd.DataFrame | None, daily: bool) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1) if df.columns.nlevels > 1 else df
    out = df.rename(columns=COLS)[["open", "high", "low", "close", "volume"]].astype(float).dropna(subset=["close"])
    idx = pd.DatetimeIndex(out.index)
    idx = idx.tz_localize(ET) if idx.tz is None else idx.tz_convert(ET)
    if daily:
        idx = pd.DatetimeIndex([pd.Timestamp(datetime.combine(t.date(), time(16, 0)), tz=ET) for t in idx])
    out.index = pd.DatetimeIndex(idx, name="time")
    return out[~out.index.duplicated(keep="last")].sort_index()


class YahooData(MarketDataProvider, ReferenceProvider, NewsProvider, EventsProvider):
    """One object implementing prices, reference data, news and earnings dates from Yahoo."""
    name = "yahoo (unofficial)"

    def __init__(self, delay_minutes: int = 15, per_minute: int = 120, universe: list[str] | None = None,
                 yf_module=None, now_fn=now_et):
        if yf_module is None:
            try:
                import yfinance as yf_module  # noqa: PLC0415
            except ImportError as e:
                raise DataUnavailable("yfinance is not installed: pip install yfinance") from e
        self.yf = yf_module
        self.delay_minutes = delay_minutes
        self.limiter = RateLimiter(per_minute)
        self.cache = TTLCache()
        self.universe = universe or DEFAULT_UNIVERSE
        self.now_fn = now_fn
        self._lock = threading.Lock()
        PROVIDER_STATS.setdefault("yahoo", {"requests": 0, "errors": 0, "cache_hits": 0, "last_ok": None})

    # ------------------------------------------------------------ plumbing
    @staticmethod
    def _sym(symbol: str) -> str:
        return symbol.replace(".", "-")  # Yahoo uses BRK-B, not BRK.B

    def _call(self, key: str, ttl: float | None, fn):
        hit = self.cache.get(key)
        stats = PROVIDER_STATS["yahoo"]
        if hit is not None:
            stats["cache_hits"] += 1
            return hit
        self.limiter.acquire()
        stats["requests"] += 1
        try:
            val = fn()
        except Exception as e:  # noqa: BLE001 — yfinance raises many exception types
            stats["errors"] += 1
            record_error("yahoo", f"{key}: {type(e).__name__}: {e}")
            raise DataUnavailable(f"yahoo: {key} failed ({type(e).__name__})") from e
        stats["last_ok"] = datetime.now(UTC).timestamp()
        self.cache.set(key, val, ttl)
        return val

    def _ticker(self, symbol):
        return self.yf.Ticker(self._sym(symbol))

    # ------------------------------------------------------------ prices
    def daily_bars(self, symbol, start, end):
        today = self.now_fn().date()
        ttl = 120 if end >= today else 6 * 3600

        def fetch():
            return _clean_bars(self._ticker(symbol).history(start=start, end=end + timedelta(days=1), interval="1d",
                                                            auto_adjust=True, actions=False), daily=True)
        df = self._call(f"daily:{symbol}:{start}:{end}", ttl, fetch)
        if df.empty:
            raise DataUnavailable(f"yahoo: no daily bars for {symbol}")
        df = df.copy()
        now = self.now_fn()
        if df.index[-1].date() == now.date() and now.time() < time(16, 0):
            df.attrs["partial_last_bar"] = True  # today's bar is still forming
        return df

    def intraday_bars(self, symbol, day, minutes=1, extended=True):
        if (self.now_fn().date() - day).days > 29:
            raise DataUnavailable("yahoo: 1-minute bars are only available for about the last 30 days")
        closed = day < self.now_fn().date()

        def fetch():
            return _clean_bars(self._ticker(symbol).history(start=day, end=day + timedelta(days=1), interval="1m",
                                                            prepost=True, auto_adjust=False, actions=False),
                               daily=False)
        df = self._call(f"intraday:{symbol}:{day}", None if closed else 60, fetch)
        df = df[df.index.date == day] if not df.empty else df
        if not extended and not df.empty:
            df = df[(df.index.time >= time(9, 30)) & (df.index.time < time(16, 0))]
        if minutes > 1 and not df.empty:
            from ..indicators import resample  # noqa: PLC0415
            df = resample(df, minutes)
        return df

    def quote(self, symbol):
        now = self.now_fn()
        bars = None
        try:
            bars = self.intraday_bars(symbol, now.date(), 1, extended=True)
        except DataUnavailable:
            pass
        if bars is not None and not bars.empty:
            ts = bars.index[-1].to_pydatetime() + timedelta(minutes=1)
            return Quote(symbol, float(bars["close"].iloc[-1]), min(ts, now), self.name, self.delay_minutes)
        d = self.daily_bars(symbol, now.date() - timedelta(days=10), now.date())
        return Quote(symbol, float(d["close"].iloc[-1]), d.index[-1].to_pydatetime(), self.name, self.delay_minutes)

    # ------------------------------------------------------------ reference
    def _info(self, symbol) -> dict:
        return self._call(f"info:{symbol}", 6 * 3600, lambda: dict(self._ticker(symbol).info or {}))

    def list_universe(self):
        out = []
        for s in self.universe:
            try:
                out.append(self.ticker_info(s))
            except DataUnavailable as e:
                log.warning("skipping %s: %s", s, e)
        return out

    def ticker_info(self, symbol):
        i = self._info(symbol)
        if not i:
            raise DataUnavailable(f"yahoo: no reference data for {symbol}")
        sector = SECTORS.get(i.get("sector") or "")
        return TickerInfo(
            symbol=symbol, name=i.get("longName") or i.get("shortName") or symbol,
            exchange=EXCHANGES.get(i.get("exchange") or "", i.get("exchange") or "UNKNOWN"),
            security_type=TYPES.get(i.get("quoteType") or "", i.get("quoteType") or ""),
            market_cap=i.get("marketCap"), shares_float=i.get("floatShares"), sector=sector,
            industry=i.get("industry"), source=self.name, as_of=self.now_fn(), sector_etf=sector_etf(sector),
            shares_outstanding=i.get("sharesOutstanding"),
        )

    def reverse_splits(self, symbol, since: date) -> list[dict]:
        sp = self._call(f"splits:{symbol}", 24 * 3600, lambda: self._ticker(symbol).splits)
        out = []
        if sp is None or len(sp) == 0:
            return out
        for ts, ratio in sp.items():
            d = pd.Timestamp(ts).date()
            if d >= since and 0 < float(ratio) < 1:
                out.append({"date": d.isoformat(), "ratio": f"1-for-{round(1 / float(ratio))}"})
        return out

    # ------------------------------------------------------------ news
    def company_news(self, symbol, since, limit=20):
        raw = self._call(f"news:{symbol}", 1800, lambda: list(self._ticker(symbol).get_news(count=max(limit, 10)) or []))
        out = []
        for it in raw:
            n = self._parse_news(it, symbol)
            if n and n.published_at >= since:
                out.append(n)
        return out[:limit]

    @staticmethod
    def _parse_news(it: dict, symbol: str) -> NewsItem | None:
        c = it.get("content") if isinstance(it.get("content"), dict) else None
        try:
            if c:  # current Yahoo format
                title = c.get("title") or ""
                pub = datetime.fromisoformat((c.get("pubDate") or c.get("displayTime")).replace("Z", "+00:00"))
                url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or "")
                publisher = (c.get("provider") or {}).get("displayName") or "Yahoo Finance"
                summary = c.get("summary")
                stype = "press_release" if c.get("contentType") == "PRESS_RELEASE" else "news"
                nid = it.get("id") or c.get("id")
            else:  # older format
                title = it.get("title") or ""
                pub = datetime.fromtimestamp(int(it["providerPublishTime"]), tz=UTC)
                url, publisher, summary, stype = it.get("link") or "", it.get("publisher") or "Yahoo Finance", None, "news"
                nid = it.get("uuid")
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if not title or not url:
            return None
        return NewsItem(id=f"yf-{nid or hash(url)}", symbols=[symbol], headline=title, publisher=publisher, url=url,
                        published_at=pub.astimezone(ET), source="yahoo", summary=summary, source_type=stype)

    # ------------------------------------------------------------ earnings
    def earnings(self, symbols, start, end):
        out = []
        for sym in symbols:
            try:
                df = self._call(f"earn:{sym}", 24 * 3600, lambda s=sym: self._ticker(s).get_earnings_dates(limit=12))
            except DataUnavailable:
                continue
            if df is None or len(df) == 0:
                continue
            for ts in df.index:
                t = pd.Timestamp(ts)
                t = t.tz_localize(ET) if t.tz is None else t.tz_convert(ET)
                if start <= t.date() <= end:
                    tod = "bmo" if t.hour < 9 else "amc" if t.hour >= 16 else None
                    out.append(Event(sym, "earnings", t.date(), "Earnings report (Yahoo, unofficial)", self.name,
                                     tod, "high"))
        return out
