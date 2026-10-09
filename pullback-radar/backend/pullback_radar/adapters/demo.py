"""DEMO MODE ONLY — deterministic synthetic market used to exercise the app without API keys.

Everything here is invented: tickers carry a DEMO prefix and digits so they cannot be mistaken for
real listings, company names end in "(synthetic)", news links point to example.com, and every payload
is tagged synthetic=True so the UI shows a permanent DEMO banner. Benchmarks (SPY, QQQ, ...) keep their
symbols so the regime engine works, but their prices are synthetic too.

Scenarios are built to cover the cases the scanner must handle: healthy pullbacks at each status,
breakdowns, pump-like spikes, event-driven drops, earnings blackouts, illiquid and excluded securities."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from ..market_calendar import ET, OPEN, is_trading_day, last_completed_session, now_et, prev_trading_day, \
    session_close, session_state
from .base import (DataUnavailable, Event, EventsProvider, Filing, FilingsProvider, MarketDataProvider, NewsItem,
                   NewsProvider, Quote, ReferenceProvider, SentimentProvider, SocialStats, TickerInfo)
from .sectors import SECTOR_ETFS, sector_etf

DEMO_SOURCE = "demo (synthetic)"


@dataclass
class DemoStock:
    symbol: str
    name: str
    sector: str
    market_cap: float
    price: float
    avg_volume: float
    scenario: str
    intraday: str = "chop"
    exchange: str = "XNAS"
    security_type: str = "CS"
    float_shares: float | None = None
    extra: dict = field(default_factory=dict)


def _d(sym, name, sector, mcap, price, vol, scen, intra="chop", **kw):
    return DemoStock(sym, f"{name} (synthetic)", sector, mcap, price, vol, scen, intra, **kw)


DEMO_STOCKS: list[DemoStock] = [
    _d("DEMO01", "Demo Alpha Semiconductor", "Technology", 85e9, 182.0, 9e6, "pullback_trigger", "vwap_reclaim"),
    _d("DEMO02", "Demo Beacon Software", "Technology", 24e9, 96.0, 4e6, "pullback_approaching", "orb_retest"),
    _d("DEMO03", "Demo Cobalt Health", "Health Care", 6.5e9, 54.0, 2.2e6, "pullback_watch", "chop"),
    _d("DEMO04", "Demo Delta Industrials", "Industrials", 3.1e9, 41.0, 1.6e6, "pullback_trigger", "orb_retest"),
    _d("DEMO05", "Demo Ember Energy", "Energy", 14e9, 67.0, 3.5e6, "breakdown", "fade"),
    _d("DEMO06", "Demo Flux Biotech", "Health Care", 410e6, 3.4, 48e6, "pump", "gap_fade", float_shares=9e6),
    _d("DEMO07", "Demo Garnet Retail", "Consumer Discretionary", 5.2e9, 38.0, 3e6, "earnings_drop", "fade"),
    _d("DEMO08", "Demo Harbor Bank", "Financials", 1.4e9, 27.0, 0.9e6, "earnings_soon", "chop"),
    _d("DEMO09", "Demo Iris Robotics", "Industrials", 950e6, 18.0, 60e3, "pullback_trigger", "chop"),  # illiquid
    _d("DEMO10", "Demo Juniper Mining", "Materials", 160e6, 6.2, 2e6, "pullback_trigger", "chop"),  # micro-cap
    _d("DEMO11", "Demo Kestrel Networks", "Technology", 1.8e9, 22.0, 2.5e6, "downtrend", "fade"),
    _d("DEMO12", "Demo Lumen Utilities", "Utilities", 12e9, 61.0, 2e6, "choppy", "chop"),
    _d("DEMO13", "Demo Meridian Media", "Communication Services", 32e9, 118.0, 5e6, "pullback_approaching",
       "vwap_reclaim"),
    _d("DEMO14", "Demo Nimbus Cloud", "Technology", 2.6e9, 74.0, 1.9e6, "extended", "orb_retest"),
    _d("DEMO15", "Demo Orchid Foods", "Consumer Staples", 7.8e9, 49.0, 2.1e6, "pullback_approaching", "chop"),
    _d("DEMO16", "Demo Pylon Realty", "Real Estate", 4.4e9, 33.0, 1.7e6, "invalidated", "fade"),
    _d("DEMO17", "Demo Quartz Payments", "Financials", 48e9, 212.0, 3.8e6, "pullback_approaching", "orb_retest"),
    _d("DEMO18", "Demo Radiant Solar", "Utilities", 620e6, 4.1, 30e6, "spike_reversal", "gap_fade", float_shares=14e6),
    _d("DEMODX", "Demo Index Tracker Fund", "Technology", 9e9, 88.0, 6e6, "pullback_trigger", "chop",
       security_type="ETF", exchange="ARCX"),
    _d("DEMOOT", "Demo Pink Sheet Holdings", "Industrials", 500e6, 8.0, 3e6, "pullback_trigger", "chop",
       exchange="OTC"),
]
BENCHMARKS = {
    "SPY": ("SPDR S&P 500 ETF (synthetic prices)", 560.0, 70e6, "bench_up"),
    "QQQ": ("Invesco QQQ (synthetic prices)", 480.0, 40e6, "bench_up"),
    "DIA": ("SPDR Dow Jones ETF (synthetic prices)", 420.0, 4e6, "bench_flat_up"),
    "IWM": ("iShares Russell 2000 (synthetic prices)", 215.0, 30e6, "bench_flat_up"),
}
for _sector, _etf in SECTOR_ETFS.items():
    BENCHMARKS[_etf] = (f"{_sector} Select Sector SPDR (synthetic prices)", 80.0, 8e6,
                        {"Technology": "bench_up", "Energy": "bench_down", "Utilities": "bench_flat",
                         "Real Estate": "bench_down"}.get(_sector, "bench_flat_up"))

# Shape of the last bars for each scenario: (bars, total % change, volume multiple).
SHAPES = {
    "pullback_trigger": [(14, 14, 1.5), (7, -6.5, 0.6), (1, 2.2, 1.6)],
    "pullback_approaching": [(14, 13, 1.5), (7, -6.0, 0.6), (1, 0.5, 0.7)],
    "pullback_watch": [(14, 8, 1.5), (5, -4.5, 0.7)],
    "breakdown": [(14, 10, 1.3), (6, -9, 1.6), (2, -6, 2.6)],
    "pump": [(25, 0, 0.6), (5, 130, 16.0)],
    "spike_reversal": [(20, 0, 0.6), (5, 95, 12.0), (4, -38, 6.0)],
    "earnings_drop": [(14, 9, 1.3), (1, -14, 4.5), (3, 1.0, 1.4)],
    "earnings_soon": [(14, 13, 1.5), (7, -6.0, 0.6), (1, 1.8, 1.5)],
    "downtrend": [(20, -8, 1.1), (6, 3, 0.8)],
    "choppy": [(26, 0, 1.0)],
    "extended": [(20, 24, 1.8)],
    "invalidated": [(14, 12, 1.4), (7, -6.5, 0.7), (1, 0.6, 0.8), (1, -5.5, 2.2)],
    "bench_up": [(26, 3.5, 1.0)],
    "bench_flat_up": [(26, 1.5, 1.0)],
    "bench_flat": [(26, 0.2, 1.0)],
    "bench_down": [(26, -4.0, 1.1)],
}
BASE_DRIFT = {"downtrend": -0.0009, "choppy": 0.0, "pump": -0.0005, "spike_reversal": -0.0006,
              "bench_flat": 0.0002, "bench_down": -0.0002}


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def _trading_days_back(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _path(rng, n, drift, sigma, shape) -> tuple[np.ndarray, np.ndarray]:
    """Log-return path: random-walk history followed by deterministic shape segments."""
    tail = sum(b for b, _, _ in shape)
    head = n - tail
    rets = drift + sigma * rng.standard_normal(head)
    vols = np.exp(0.25 * rng.standard_normal(head))
    for bars, pct, vm in shape:
        target = np.log1p(pct / 100)
        noise = sigma * 0.35 * rng.standard_normal(bars)
        seg = target / bars + noise - noise.mean()
        rets = np.concatenate([rets, seg])
        vols = np.concatenate([vols, vm * np.exp(0.15 * rng.standard_normal(bars))])
    return rets, vols


class DemoMarket(MarketDataProvider, ReferenceProvider, NewsProvider, EventsProvider, FilingsProvider,
                 SentimentProvider):
    """Single object implementing every adapter interface with synthetic data."""
    name = DEMO_SOURCE
    synthetic = True
    delay_minutes = 0

    def __init__(self, now: datetime | None = None, history: int = 320):
        self._now = now
        self.history = history
        self.stocks = {s.symbol: s for s in DEMO_STOCKS}
        self._daily_cache: dict = {}

    # ------------------------------------------------------------ clock
    def now(self) -> datetime:
        return (self._now or now_et()).astimezone(ET)

    def _meta(self, symbol):
        if symbol in self.stocks:
            s = self.stocks[symbol]
            return s.price, s.avg_volume, s.scenario, s.intraday
        if symbol in BENCHMARKS:
            _, p, v, scen = BENCHMARKS[symbol]
            return p, v, scen, "bench_intraday"
        raise DataUnavailable(f"demo: unknown symbol {symbol}")

    # ------------------------------------------------------------ prices
    def _completed_daily(self, symbol) -> pd.DataFrame:
        end = last_completed_session(self.now())
        key = (symbol, end)
        if key in self._daily_cache:
            return self._daily_cache[key]
        price, avg_vol, scen, _ = self._meta(symbol)
        rng = np.random.default_rng(_seed(symbol, "daily"))
        n = self.history
        sigma = 0.028 if scen in ("pump", "spike_reversal") else 0.009 if scen.startswith("bench") else 0.011
        drift = BASE_DRIFT.get(scen, 0.0016 if not scen.startswith("bench") else 0.0006)
        rets, vm = _path(rng, n, drift, sigma, SHAPES[scen])
        close = price * np.exp(np.cumsum(rets) - np.sum(rets))  # last close == configured price
        prev = np.concatenate([[close[0] / np.exp(rets[0])], close[:-1]])
        gap = np.where(np.abs(rets) > 0.08, rets * 0.85, rets * 0.3 + 0.002 * rng.standard_normal(n))
        opn = prev * np.exp(gap)
        wick = np.abs(rng.standard_normal(n)) * sigma * 0.45 + sigma * 0.15
        high = np.maximum(opn, close) * (1 + wick * 0.6)
        low = np.minimum(opn, close) * (1 - wick * 0.6)
        if scen in ("pullback_trigger", "earnings_soon"):
            # Final bar: hammer-style reversal that closes in the top of its range.
            low[-1] = min(opn[-1], close[-1]) * (1 - sigma * 0.9)
            high[-1] = close[-1] * 1.002
        volume = np.round(avg_vol * vm).astype(float)
        days = _trading_days_back(end, n)
        idx = pd.DatetimeIndex([pd.Timestamp(datetime.combine(d, time(16, 0)), tz=ET) for d in days], name="time")
        df = pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
        df = df.round({"open": 2, "high": 2, "low": 2, "close": 2})
        df["high"] = df[["open", "high", "close"]].max(axis=1)
        df["low"] = df[["open", "low", "close"]].min(axis=1)
        self._daily_cache[key] = df
        return df

    def daily_bars(self, symbol, start, end):
        df = self._completed_daily(symbol)
        now = self.now()
        today = now.date()
        if is_trading_day(today) and session_state(now) == "open":
            intra = self.intraday_bars(symbol, today, 1, extended=False)
            if not intra.empty:
                bar = pd.DataFrame({"open": [intra["open"].iloc[0]], "high": [intra["high"].max()],
                                    "low": [intra["low"].min()], "close": [intra["close"].iloc[-1]],
                                    "volume": [intra["volume"].sum()]},
                                   index=pd.DatetimeIndex([pd.Timestamp(datetime.combine(today, time(16, 0)), tz=ET)],
                                                          name="time"))
                bar.attrs["partial"] = True
                df = pd.concat([df, bar])
                df.attrs["partial_last_bar"] = True
        sel = df[(df.index.date >= start) & (df.index.date <= end)]
        sel.attrs.update(df.attrs)
        return sel

    def intraday_bars(self, symbol, day, minutes=1, extended=True):
        now = self.now()
        if day > now.date() or not is_trading_day(day):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        _, avg_vol, _, intra = self._meta(symbol)
        daily = self._completed_daily(symbol)
        prior = daily[daily.index.date < day]
        if prior.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        prev_close = float(prior["close"].iloc[-1])
        rng = np.random.default_rng(_seed(symbol, day, "intraday"))
        start = datetime.combine(day, time(4, 0), ET)
        end = datetime.combine(day, time(20, 0), ET)
        if day == now.date():
            end = min(end, now.replace(second=0, microsecond=0))
        stamps = pd.date_range(start, end, freq="1min", inclusive="left", tz=ET)
        if len(stamps) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        full = pd.date_range(start, datetime.combine(day, time(20, 0), ET), freq="1min", inclusive="left", tz=ET)
        mins = np.array([(t.hour * 60 + t.minute) for t in full])
        reg = (mins >= 570) & (mins < (session_close(day).hour * 60))
        path = self._intraday_path(intra, mins, rng)
        close = prev_close * np.exp(path)
        sig = 0.0009
        opn = np.concatenate([[prev_close], close[:-1]])
        high = np.maximum(opn, close) * (1 + np.abs(rng.standard_normal(len(full))) * sig * 0.5)
        low = np.minimum(opn, close) * (1 - np.abs(rng.standard_normal(len(full))) * sig * 0.5)
        # U-shaped volume profile in the regular session, thin pre/post market.
        t_reg = np.clip((mins - 570) / 390, 0, 1)
        profile = np.where(reg, 0.6 + 2.4 * (2 * t_reg - 1) ** 2, 0.04)
        burst = self._intraday_volume_bursts(intra, mins)
        vol = avg_vol / 390 * profile * burst * np.exp(0.3 * rng.standard_normal(len(full)))
        df = pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": np.round(vol)},
                          index=pd.DatetimeIndex(full, name="time")).round(
            {"open": 2, "high": 2, "low": 2, "close": 2})
        df = df.loc[df.index < (stamps[-1] + pd.Timedelta(minutes=1))]
        if not extended:
            df = df[(df.index.time >= OPEN) & (df.index.time < session_close(day))]
        if minutes > 1:
            from ..indicators import resample
            df = resample(df, minutes)
        return df

    @staticmethod
    def _intraday_path(kind, mins, rng):
        """Cumulative log return vs previous close, per minute from 04:00."""
        n = len(mins)
        noise = np.cumsum(0.00015 * rng.standard_normal(n))
        knots = {  # minute-of-day -> cumulative % vs prev close
            "vwap_reclaim": [(240, 0.2), (569, 0.6), (590, 1.8), (630, 0.1), (660, 1.0), (720, 1.5), (960, 2.0)],
            "orb_retest": [(240, 0.1), (569, 0.4), (585, 0.9), (600, 1.6), (640, 1.15), (690, 1.9), (960, 2.4)],
            "fade": [(240, 0.3), (569, 0.8), (585, 1.2), (660, -0.6), (780, -1.3), (960, -1.8)],
            "gap_fade": [(240, 6.0), (569, 9.0), (600, 4.0), (720, 1.0), (960, -2.0)],
            "chop": [(240, 0.0), (569, 0.1), (660, 0.4), (780, -0.2), (960, 0.2)],
            "bench_intraday": [(240, 0.05), (569, 0.15), (620, 0.35), (720, 0.2), (960, 0.45)],
        }[kind]
        xs, ys = zip(*knots)
        base = np.interp(mins, xs, ys) / 100
        return base + noise

    @staticmethod
    def _intraday_volume_bursts(kind, mins):
        b = np.ones(len(mins))
        if kind == "vwap_reclaim":
            b[(mins >= 630) & (mins < 660)] = 2.2
        if kind == "orb_retest":
            b[(mins >= 585) & (mins < 600)] = 2.0
            b[(mins >= 640) & (mins < 690)] = 1.6
        if kind == "gap_fade":
            b[mins < 600] = 6.0
        return b

    def quote(self, symbol):
        now = self.now()
        sess = session_state(now)
        day = now.date() if is_trading_day(now.date()) and sess in ("pre", "open", "after") else None
        if day:
            bars = self.intraday_bars(symbol, day, 1, extended=True)
            if not bars.empty:
                last = bars.iloc[-1]
                px = float(last["close"])
                return Quote(symbol, px, bars.index[-1].to_pydatetime() + timedelta(minutes=1), DEMO_SOURCE, 0,
                             bid=round(px * 0.9997, 2), ask=round(px * 1.0003, 2))
        d = self._completed_daily(symbol)
        px = float(d["close"].iloc[-1])
        spread = 0.012 if self.stocks.get(symbol) and self.stocks[symbol].avg_volume < 1e5 else 0.0004
        return Quote(symbol, px, d.index[-1].to_pydatetime(), DEMO_SOURCE, 0,
                     bid=round(px * (1 - spread / 2), 2), ask=round(px * (1 + spread / 2), 2))

    # ------------------------------------------------------------ reference
    def list_universe(self):
        return [self.ticker_info(s) for s in self.stocks]

    def ticker_info(self, symbol):
        if symbol in BENCHMARKS:
            return TickerInfo(symbol, BENCHMARKS[symbol][0], "ARCX", "ETF", None, None, None, None, DEMO_SOURCE,
                              self.now())
        s = self.stocks.get(symbol)
        if not s:
            raise DataUnavailable(f"demo: unknown symbol {symbol}")
        shares = s.market_cap / s.price
        return TickerInfo(s.symbol, s.name, s.exchange, s.security_type, s.market_cap,
                          s.float_shares or shares * 0.85, s.sector, f"{s.sector} (synthetic)", DEMO_SOURCE,
                          self.now(), sector_etf(s.sector), shares_outstanding=shares)

    def reverse_splits(self, symbol, since):
        if symbol == "DEMO06":
            return [{"date": (self.now().date() - timedelta(days=120)).isoformat(), "ratio": "1-for-20"}]
        return []

    # ------------------------------------------------------------ news
    def _news(self, symbol) -> list[NewsItem]:
        s = self.stocks.get(symbol)
        if not s:
            return []
        now = self.now()
        last = last_completed_session(now)

        def item(i, days_ago, headline, publisher, stype, sentiment=None, summary=None):
            d = last
            for _ in range(days_ago):
                d = prev_trading_day(d)
            pub = datetime.combine(d, time(8, 5), ET) + timedelta(minutes=17 * i)
            return NewsItem(id=f"demo-{symbol}-{i}", symbols=[symbol], headline=headline, publisher=publisher,
                            url=f"https://example.com/demo-news/{symbol.lower()}-{i}", published_at=pub,
                            source=DEMO_SOURCE, summary=summary, provider_sentiment=sentiment, source_type=stype)

        wire, blog, social = "Demo Newswire (synthetic)", "Demo Market Blog (synthetic)", "Demo Social Feed (synthetic)"
        sc = s.scenario
        if sc == "pullback_trigger":
            return [item(1, 12, f"{s.name} raises full-year revenue guidance after record quarter", wire,
                         "press_release", "positive"),
                    item(2, 3, f"Analyst at Demo Securities upgrades {symbol} to Buy, citing demand", wire, "analyst",
                         "positive")]
        if sc == "pullback_approaching":
            return [item(1, 9, f"{s.name} wins multi-year contract with large customer", wire, "press_release",
                         "positive")]
        if sc == "breakdown":
            return [item(1, 1, f"{s.name} cuts outlook as demand weakens; shares slide", wire, "press_release",
                         "negative")]
        if sc == "pump":
            return [item(1, 0, f"Is {symbol} the next 100x rocket? Shares could soar 1,000%!", blog, "opinion"),
                    item(2, 0, f"${symbol} to the moon — everyone is buying", social, "social")]
        if sc == "spike_reversal":
            return [item(1, 2, f"{s.name} files for $40 million at-the-market share offering", wire, "filing",
                         "negative")]
        if sc == "earnings_drop":
            return [item(1, 3, f"{s.name} misses quarterly estimates and lowers guidance", wire, "press_release",
                         "negative")]
        if sc == "earnings_soon":
            return [item(1, 5, f"{s.name} to report quarterly results next week", wire, "press_release", "neutral")]
        if sc == "invalidated":
            return [item(1, 0, f"Regulators open review of {s.name} lending practices", wire, "news", "negative")]
        return []

    def company_news(self, symbol, since, limit=20):
        return [n for n in self._news(symbol) if n.published_at >= since][:limit]

    def market_news(self, since, limit=20):
        last = last_completed_session(self.now())
        items = [
            NewsItem("demo-mkt-1", [], "Demo: Fed officials signal patience on rates (synthetic headline)",
                     "Demo Newswire (synthetic)", "https://example.com/demo-news/market-1",
                     datetime.combine(last, time(14, 0), ET), DEMO_SOURCE, provider_sentiment="neutral",
                     source_type="news"),
            NewsItem("demo-mkt-2", [], "Demo: Chipmakers lead gains as tech rebounds (synthetic headline)",
                     "Demo Newswire (synthetic)", "https://example.com/demo-news/market-2",
                     datetime.combine(last, time(16, 30), ET), DEMO_SOURCE, provider_sentiment="positive",
                     source_type="news"),
        ]
        return [n for n in items if n.published_at >= since][:limit]

    # ------------------------------------------------------------ events
    def earnings(self, symbols, start, end):
        last = last_completed_session(self.now())
        out = []
        for sym in symbols:
            s = self.stocks.get(sym)
            if not s:
                continue
            if s.scenario == "earnings_soon":
                d = last
                for _ in range(2):
                    d = d + timedelta(days=1)
                    while not is_trading_day(d):
                        d += timedelta(days=1)
                ev_date = d
            elif s.scenario == "earnings_drop":
                d = last
                for _ in range(3):
                    d = prev_trading_day(d)
                ev_date = d
            else:
                ev_date = last + timedelta(days=30 + (_seed(sym) % 40))
            if start <= ev_date <= end:
                out.append(Event(sym, "earnings", ev_date, "Earnings report (synthetic date)", DEMO_SOURCE,
                                 "amc", "high"))
        return out

    def economic(self, start, end):
        last = last_completed_session(self.now())
        d = last + timedelta(days=1)
        while not is_trading_day(d):
            d += timedelta(days=1)
        ev = Event(None, "economic", d, "Demo: Consumer Price Index release (synthetic)", DEMO_SOURCE, "08:30", "high")
        return [ev] if start <= d <= end else []

    # ------------------------------------------------------------ filings & social
    def recent_filings(self, symbol, since):
        last = last_completed_session(self.now())
        if symbol == "DEMO18":
            return [Filing(symbol, "424B5", last - timedelta(days=3), "https://example.com/demo-filing/424b5",
                           "Prospectus supplement (synthetic)")]
        if symbol == "DEMO06":
            return [Filing(symbol, "S-3", last - timedelta(days=40), "https://example.com/demo-filing/s3",
                           "Shelf registration (synthetic)")]
        return []

    def social(self, symbol):
        s = self.stocks.get(symbol)
        if not s:
            return None
        if s.scenario in ("pump", "spike_reversal"):
            return SocialStats(symbol, 4200, 150.0, 0.93, DEMO_SOURCE, self.now())
        return SocialStats(symbol, 40 + _seed(symbol) % 60, 55.0, 0.55, DEMO_SOURCE, self.now())
