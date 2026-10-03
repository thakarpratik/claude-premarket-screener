"""Pre-market catalyst screen -> HTML dashboard.

Scans liquid U.S. stocks (S&P 500 + Nasdaq-100 + watchlist.txt) for unusual
moves versus the previous close, pulls macro/cross-asset context, headlines,
analyst actions and earnings, and writes a browser dashboard.

Usage:
  python premarket_screen.py              # build once and open in browser
  python premarket_screen.py --watch 5    # rebuild every 5 min until 09:35 ET
  python premarket_screen.py --no-open    # build without opening a browser

Data: Yahoo Finance (via yfinance) and Nasdaq public calendars. Free data is
delayed/incomplete in places; the dashboard labels what it could not verify.
This is a research screen, not investment advice.
"""
import argparse
import html
import io
import json
import logging
import math
import re
import time
import webbrowser
import xml.etree.ElementTree as XML
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

import learning
import publish

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
UNIVERSE_CACHE = HERE / "universe_cache.json"
WATCHLIST = HERE / "watchlist.txt"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}

MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 25e6      # 20-day average daily dollar volume
MIN_ABS_GAP_PCT = 1.0
TOP_TABLE = 25
TOP_CARDS = 10
NEWS_MAX_AGE_H = 36
MIN_PICK_MCAP = 2e9        # long setups below this are excluded as pump-and-dump risk
LOW_FLOAT = 20e6           # float shares below this are easy to squeeze or pump

MACRO = {
    "U.S. futures": [("S&P 500 fut", "ES=F"), ("Nasdaq-100 fut", "NQ=F"),
                     ("Dow fut", "YM=F"), ("Russell 2000 fut", "RTY=F"), ("VIX", "^VIX")],
    "Rates & USD": [("2Y yield (fut)", "2YY=F"), ("10Y yield", "^TNX"),
                    ("30Y yield", "^TYX"), ("Dollar index", "DX-Y.NYB"), ("USD/JPY", "JPY=X")],
    "Commodities & crypto": [("WTI crude", "CL=F"), ("Brent crude", "BZ=F"), ("Nat gas", "NG=F"),
                             ("Gold", "GC=F"), ("Copper", "HG=F"), ("Bitcoin", "BTC-USD"),
                             ("Ether", "ETH-USD")],
    "Global equities": [("Nikkei 225", "^N225"), ("Hang Seng", "^HSI"), ("Shanghai", "000001.SS"),
                        ("Kospi", "^KS11"), ("Euro Stoxx 50", "^STOXX50E"), ("DAX", "^GDAXI"),
                        ("FTSE 100", "^FTSE")],
}
YIELD_SYMBOLS = {"2YY=F", "^TNX", "^TYX"}

SECTOR_ETFS = [("Tech", "XLK"), ("Semis", "SMH"), ("Financials", "XLF"), ("Reg. banks", "KRE"),
               ("Energy", "XLE"), ("Health care", "XLV"), ("Biotech", "XBI"), ("Industrials", "XLI"),
               ("Cons. disc.", "XLY"), ("Staples", "XLP"), ("Utilities", "XLU"), ("Materials", "XLB"),
               ("Real estate", "XLRE"), ("Comm. svcs", "XLC"), ("Homebuilders", "ITB"),
               ("Airlines", "JETS"), ("Long bonds", "TLT"), ("High yield", "HYG")]

FALLBACK_UNIVERSE = """AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD NFLX COST ADBE CRM ORCL INTC MU QCOM
TXN AMAT LRCX KLAC MRVL PANW CRWD SNOW PLTR UBER ABNB SHOP COIN MSTR JPM BAC WFC C GS MS SCHW BLK V MA
PYPL AXP UNH LLY JNJ PFE MRK ABBV AMGN GILD REGN VRTX MRNA BMY CVS XOM CVX COP OXY SLB HAL MPC VLO PSX
BA CAT DE GE HON LMT RTX NOC UPS FDX WMT TGT HD LOW NKE SBUX MCD DIS CMCSA T VZ KO PEP PG CCL RCL NCLH
DAL UAL AAL F GM RIVN LCID SMCI ARM DELL HPQ IBM CSCO""".split()


# ---------------------------------------------------------------- helpers

def now_et() -> datetime:
    return datetime.now(ET)


def session_state(t: datetime) -> str:
    if t.weekday() >= 5:
        return "Closed (weekend)"
    hm = t.hour * 60 + t.minute
    if hm < 4 * 60:
        return "Closed (overnight)"
    if hm < 9 * 60 + 30:
        return "Pre-market"
    if hm < 16 * 60:
        return "Regular session open"
    if hm < 20 * 60:
        return "After-hours"
    return "Closed"


def fnum(x, d=2, pct=False, sign=False, dollar=False):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    if pct:
        s += "%"
    if dollar:
        s = ("-$" + s[1:]) if s.startswith("-") else "$" + s.lstrip("+")
    return s


def fbig(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"{x / div:.1f}{suf}"
    return f"{x:.0f}"


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


def cls(x) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    return "up" if x > 0 else "down" if x < 0 else ""


def to_et_index(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    df = df.copy()
    df.index = idx.tz_convert(ET)
    return df


def per_ticker(df: pd.DataFrame, t: str):
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        if t not in df.columns.get_level_values(0):
            return None
        sub = df[t]
    else:
        sub = df
    sub = sub.dropna(how="all")
    return sub if not sub.empty else None


def rsi(close: pd.Series, n: int = 14) -> float:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    r = 100 - 100 / (1 + up / dn)
    return float(r.iloc[-1])


def atr(h: pd.DataFrame, n: int = 14) -> float:
    pc = h["Close"].shift(1)
    tr = pd.concat([h["High"] - h["Low"], (h["High"] - pc).abs(), (h["Low"] - pc).abs()], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])


# ---------------------------------------------------------------- universe

def load_universe() -> list[str]:
    tickers: list[str] = []
    try:
        cached = json.loads(UNIVERSE_CACHE.read_text())
        if datetime.fromisoformat(cached["saved"]) > datetime.now() - timedelta(days=7):
            tickers = cached["tickers"]
    except Exception:
        pass
    if not tickers:
        for url in ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                    "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"):
            try:
                page = requests.get(url, headers=UA, timeout=20).text
                for tbl in pd.read_html(io.StringIO(page)):
                    col = next((c for c in ("Symbol", "Ticker symbol", "Ticker") if c in tbl.columns), None)
                    if col and len(tbl) > 90:
                        tickers += [str(s).strip().replace(".", "-") for s in tbl[col]]
                        break
            except Exception as e:
                print(f"  universe fetch failed for {url}: {e}")
        try:
            r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100", headers=UA, timeout=15)
            tickers += [row["symbol"].replace(".", "-") for row in r.json()["data"]["data"]["rows"]]
        except Exception as e:
            print(f"  Nasdaq-100 fetch failed: {e}")
        tickers = sorted(set(tickers))
        if len(tickers) > 400:
            UNIVERSE_CACHE.write_text(json.dumps({"saved": datetime.now().isoformat(), "tickers": tickers}))
        else:
            print("  using built-in fallback universe")
            tickers = FALLBACK_UNIVERSE[:]
    if WATCHLIST.exists():
        for line in WATCHLIST.read_text().splitlines():
            t = line.split("#")[0].strip().upper()
            if t:
                tickers.append(t)
    return sorted(set(tickers))


# ---------------------------------------------------------------- market data

def fetch_macro() -> dict:
    syms = [s for grp in MACRO.values() for _, s in grp]
    df = yf.download(syms, period="10d", interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    out = {}
    for grp, items in MACRO.items():
        rows = []
        for label, s in items:
            sub = per_ticker(df, s)
            if sub is None or sub["Close"].dropna().shape[0] < 2:
                rows.append({"label": label, "sym": s, "last": None, "chg": None, "asof": "Unavailable"})
                continue
            c = sub["Close"].dropna()
            last, prev = float(c.iloc[-1]), float(c.iloc[-2])
            if s in YIELD_SYMBOLS:
                chg, unit = (last - prev) * 100, "bp"
            else:
                chg, unit = (last / prev - 1) * 100, "%"
            # Yahoo's continuous futures series jumps when the front month rolls.
            roll = s.endswith("=F") and unit == "%" and abs(chg) > 6
            rows.append({"label": label, "sym": s, "last": last, "chg": chg, "unit": unit,
                         "asof": c.index[-1].strftime("%b %d"), "yield": s in YIELD_SYMBOLS, "roll": roll})
        out[grp] = rows
    return out


def fetch_daily(tickers: list[str]) -> pd.DataFrame:
    return yf.download(tickers, period="1y", interval="1d", group_by="ticker",
                       auto_adjust=False, progress=False, threads=True)


def fetch_intraday(tickers: list[str]) -> pd.DataFrame:
    frames = []
    for i in range(0, len(tickers), 150):
        chunk = tickers[i:i + 150]
        try:
            frames.append(yf.download(chunk, period="2d", interval="5m", prepost=True,
                                      group_by="ticker", auto_adjust=False, progress=False, threads=True))
        except Exception as e:
            print(f"  intraday chunk failed: {e}")
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def compute_metrics(tickers, daily, intraday, today: date) -> pd.DataFrame:
    rows = []
    for t in tickers:
        d = per_ticker(daily, t)
        if d is None:
            continue
        d = d.dropna(subset=["Close"])
        hist = d[[ix.date() < today for ix in d.index]]
        if len(hist) < 30:
            continue
        prev_close = float(hist["Close"].iloc[-1])
        last20 = hist.tail(20)
        adv = float(last20["Volume"].mean())
        dollar_vol = float((last20["Close"] * last20["Volume"]).mean())

        price, pm_vol, pm_hi, pm_lo, last_bar = None, None, None, None, None
        i = per_ticker(intraday, t)
        if i is not None:
            i = to_et_index(i.dropna(subset=["Close"]))
            todays = i[[ix.date() == today for ix in i.index]]
            if not todays.empty:
                price = float(todays["Close"].iloc[-1])
                last_bar = todays.index[-1]
                pm = todays[[(ix.hour * 60 + ix.minute) < 570 for ix in todays.index]]
                if not pm.empty:
                    pm_vol = float(pm["Volume"].sum()) or None  # Yahoo often reports 0 pre-market
                    pm_hi, pm_lo = float(pm["High"].max()), float(pm["Low"].min())
        if price is None:
            continue

        c = hist["Close"]
        atr_v = atr(hist)
        gap = (price / prev_close - 1) * 100
        atr_pct = atr_v / prev_close * 100
        rows.append({
            "ticker": t, "price": price, "prev_close": prev_close, "gap_pct": gap,
            "atr_pct": atr_pct, "gap_atr": gap / atr_pct if atr_pct else float("nan"),
            "pm_vol": pm_vol, "pm_vol_pct_adv": (pm_vol / adv * 100) if (pm_vol and adv) else None,
            "pm_hi": pm_hi, "pm_lo": pm_lo, "adv": adv, "dollar_vol": dollar_vol,
            "prev_hi": float(hist["High"].iloc[-1]), "prev_lo": float(hist["Low"].iloc[-1]),
            "sma20": float(c.tail(20).mean()), "sma50": float(c.tail(50).mean()),
            "sma200": float(c.tail(200).mean()) if len(c) >= 200 else None,
            "rsi": rsi(c), "hi52": float(hist["High"].max()), "lo52": float(hist["Low"].min()),
            "ret20": (prev_close / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else None,
            "last_bar": last_bar,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- enrichment

def rss(url: str, max_age_h: int = NEWS_MAX_AGE_H) -> list[dict]:
    """Parse an RSS feed into [{title, url, provider, ts}] newer than max_age_h."""
    out = []
    cutoff = datetime.now(ET) - timedelta(hours=max_age_h)
    try:
        r = requests.get(url, headers={"User-Agent": UA["User-Agent"]}, timeout=15)
        root = XML.fromstring(r.content)
    except Exception:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate")).astimezone(ET)
        except Exception:
            continue
        src = it.find("source")
        prov = src.text if src is not None and src.text else ""
        if prov and title.endswith(" - " + prov):
            title = title[: -len(prov) - 3]
        if title and ts >= cutoff:
            out.append({"title": title, "url": it.findtext("link"), "provider": prov, "ts": ts})
    return out


def company_news(t: str, name: str) -> list[dict]:
    items = rss(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US")
    short = re.sub(r"[,.]?\s+(Inc|Corp|Corporation|Company|Co|Ltd|plc|Holdings|Group|N\.V|S\.A)\.?$", "",
                   name or "", flags=re.I).strip()
    q = quote_plus(f'"{short}" OR "{t} stock" when:2d') if short else quote_plus(f'"{t} stock" when:2d')
    items += rss(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
    # Keep only headlines that actually name the company (ticker, or name if it's distinctive).
    pats = [re.compile(rf"\b{re.escape(t)}\b")]
    if len(short) >= 4:
        pats.append(re.compile(rf"\b{re.escape(short)}\b", re.I))
        first = short.split()[0]
        if len(first) >= 5:
            pats.append(re.compile(rf"\b{re.escape(first)}\b"))
    items = [n for n in items if any(pt.search(n["title"]) for pt in pats)]
    seen, out = set(), []
    for n in sorted(items, key=lambda x: x["ts"], reverse=True):
        key = re.sub(r"\W+", "", n["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out[:6]


def market_headlines() -> list[dict]:
    q = quote_plus('"premarket" OR "pre-market" OR "biggest moves" stocks when:1d')
    items = rss(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", max_age_h=20)
    return sorted(items, key=lambda x: x["ts"], reverse=True)[:15]


def enrich(t: str, today: date) -> dict:
    info, news, grades, earn, actions = {}, [], [], None, []
    tk = yf.Ticker(t)
    try:
        info = tk.info or {}
    except Exception:
        pass
    news = company_news(t, info.get("shortName") or info.get("longName") or "")
    try:
        ud = tk.upgrades_downgrades
        if ud is not None and not ud.empty:
            ud = ud.reset_index()
            ud["GradeDate"] = pd.to_datetime(ud["GradeDate"])
            recent = ud[ud["GradeDate"].dt.date >= today - timedelta(days=3)]
            for _, r in recent.head(5).iterrows():
                actions.append(str(r.get("Action", "")).lower())
                pt = r.get("currentPriceTarget")
                grades.append(f"{r['GradeDate']:%b %d} · {r.get('Firm', '')}: {r.get('Action', '')} "
                              f"{r.get('FromGrade', '') or ''} → {r.get('ToGrade', '')}"
                              + (f" (PT ${pt:,.0f})" if isinstance(pt, (int, float)) and pt else ""))
    except Exception:
        pass
    try:
        cal = tk.calendar or {}
        for ed in cal.get("Earnings Date", []) or []:
            if abs((ed - today).days) <= 2:
                earn = ed
    except Exception:
        pass
    return {"info": info, "news": news, "grades": grades, "earnings": earn, "actions": actions}


# ---------------------------------------------------------------- long-setup scoring

SECTOR_TO_ETF = {"Technology": "XLK", "Financial Services": "XLF", "Energy": "XLE", "Healthcare": "XLV",
                 "Industrials": "XLI", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
                 "Utilities": "XLU", "Basic Materials": "XLB", "Real Estate": "XLRE",
                 "Communication Services": "XLC"}
GENERIC_NEWS = re.compile(r"stocks? (moving|making|to watch)|biggest mov|movers|pre-?market session|"
                          r"top (gainers|losers)|death cross|golden cross|stock forecast|price prediction|"
                          r"should you buy|stock quote|price target history|stock market today|live updates|"
                          r"market wrap|stocks to watch|futures (rise|fall|slip|climb)", re.I)


def long_score(r, e: dict, sector_moves: dict, market_chg: float | None,
               weights: dict, news_known: bool = True) -> tuple[int, list[str], list[str], dict]:
    """0-100 score for a gap-up long setup: weighted sum of features (weights are learned daily).

    Returns (score, positives, negatives, features). news_known is false on a
    historical replay, where the morning's headlines are no longer available."""
    f = {k: 0.0 for k in weights}
    pos, neg = [], []
    specific = [n for n in e["news"] if not GENERIC_NEWS.search(n["title"])]
    fresh = [n for n in specific if n["ts"] >= datetime.now(ET) - timedelta(hours=18)]
    if fresh:
        f["fresh_news"] = 1
        pos.append(f"Fresh company-specific headline ({fresh[0]['ts']:%H:%M} ET): “{fresh[0]['title'][:90]}”")
    elif specific:
        f["older_news"] = 1
        pos.append("Company-specific headline, but older than 18h")
    elif news_known:
        neg.append("No identifiable company-specific catalyst. The move may be sympathy or noise.")
    if e["earnings"]:
        f["earnings"] = 1
        pos.append(f"Earnings event {e['earnings']:%b %d}: fundamental news is repricing the stock")
    ups, downs = e["actions"].count("up"), e["actions"].count("down")
    if ups:
        f["upgrade"] = 1
        pos.append(f"{ups} analyst upgrade(s) in last 3 days")
    if downs:
        f["downgrade"] = 1
        neg.append(f"{downs} analyst downgrade(s) in last 3 days")

    g = r["gap_atr"]
    f["move_size"] = round(min(max(g, 0), 3) / 3, 3)
    pos.append(f"Move is {g:.1f}× its normal daily range")

    p = r["price"]
    if p > r["sma50"]:
        f["above_50dma"] = 1
        pos.append("Above 50-day average (trend support)")
    else:
        neg.append("Below 50-day average: gapping up inside a downtrend (short covering is common)")
    if r["sma200"] and p > r["sma200"]:
        f["above_200dma"] = 1
        pos.append("Above 200-day average")
    elif r["sma200"]:
        neg.append("Below 200-day average")
    if r["rsi"] >= 75:
        f["rsi_hot"] = 1
        neg.append(f"RSI {r['rsi']:.0f} before the gap: stretched, gap-fade risk")
    elif r["rsi"] < 70:
        f["rsi_ok"] = 1
    if r["ret20"] is not None and r["ret20"] > 30:
        f["extended_20d"] = 1
        neg.append(f"Already +{r['ret20']:.0f}% in 20 sessions: extended")

    if r["pm_hi"] and r["pm_lo"] and r["pm_hi"] > r["pm_lo"]:
        where = (p - r["pm_lo"]) / (r["pm_hi"] - r["pm_lo"])
        if where >= 0.8:
            f["holding_gap"] = 1
            pos.append("Trading near pre-market high (buyers holding the gap)")
        elif where < 0.4:
            f["fading_gap"] = 1
            neg.append("Well off pre-market high: gap is fading already")

    etf = SECTOR_TO_ETF.get(e["info"].get("sector", ""))
    if etf and etf in sector_moves:
        if sector_moves[etf] > 0:
            f["sector_green"] = 1
            pos.append(f"Sector ({etf}) also green ({sector_moves[etf]:+.2f}%)")
        else:
            f["sector_red"] = 1
            neg.append(f"Sector ({etf}) is red ({sector_moves[etf]:+.2f}%): move lacks sector confirmation")
    if market_chg is not None:
        if market_chg > 0:
            f["market_green"] = 1
        else:
            neg.append("S&P futures red: weak tape")
    if r["dollar_vol"] >= 100e6:
        f["liquid"] = 1

    # Pump-and-dump guards
    mcap = None if pd.isna(r["mcap"]) else r["mcap"]
    tier, _ = cap_tier(mcap)
    if mcap is None:
        f["mcap_unknown"] = 1
        neg.append("Market cap unknown: can't rule out a thinly traded name")
    elif mcap >= 10e9:
        f["large_cap"] = 1
        pos.append(f"{tier} cap (${fbig(mcap)}): too large to pump")
    else:
        pos.append(f"{tier} cap (${fbig(mcap)})")
    flt = e["info"].get("floatShares")
    if flt and flt < LOW_FLOAT:
        f["low_float"] = 1
        neg.append(f"Low float ({fbig(flt)} shares): easy to squeeze or pump, high reversal risk")
    if news_known and g > 5 and not specific:
        f["pump_pattern"] = 1
        neg.append(f"{g:.0f}× normal range with no company news: classic pump pattern")
    if r["ret20"] is not None and r["ret20"] > 100:
        f["parabolic"] = 1
        neg.append(f"Up {r['ret20']:.0f}% in 20 sessions before today: parabolic")
    return learning.score_from(f, weights), pos, neg, f


def levels(r) -> tuple[float, float]:
    """(trigger, invalidation) for a long setup."""
    def num(x):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        return float(x)
    price = num(r["price"])
    trigger = num(r["pm_hi"]) or price
    stop = min(num(r["pm_lo"]) or num(r["prev_close"]), price * 0.99)
    return trigger, stop


DEFAULT_BROKER = {
    "label": "Open chart",
    "url": "https://www.tradingview.com/chart/?symbol={ticker}",
}


def broker_config() -> dict:
    """Link opened from a setup card. Override with broker.json."""
    path = HERE / "broker.json"
    if not path.exists():
        return dict(DEFAULT_BROKER)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  broker.json ignored: {e}")
        return dict(DEFAULT_BROKER)
    url = str(data.get("url") or "")
    label = str(data.get("label") or DEFAULT_BROKER["label"]).strip()[:40]
    if url.startswith("https://") and "{ticker}" in url and label:
        return {"label": label, "url": url}
    print("  broker.json ignored: url must start with https:// and include {ticker}")
    return dict(DEFAULT_BROKER)


def broker_button(ticker: str, broker: dict) -> str:
    url = broker["url"].replace("{ticker}", quote(ticker, safe=""))
    return (f"<div class=act><a href='{esc(url)}' target=_blank rel=noopener>"
            f"{esc(broker['label'])}</a></div>")


def picks_html(picks: list, broker: dict | None = None) -> str:
    if not picks:
        return "<div class=card><p>No liquid stock is gapping up ≥1% with enough evidence to score yet.</p></div>"
    broker = broker or DEFAULT_BROKER
    out = []
    for rank, (score, r, e, pos, neg, _) in enumerate(picks, 1):
        name = e["info"].get("shortName") or r["name"]
        mcap = None if pd.isna(r["mcap"]) else r["mcap"]
        trigger, stop = levels(r)
        risk_pct = (trigger - stop) / trigger * 100
        grade = "Strong" if score >= 70 else "Moderate" if score >= 50 else "Weak"
        gcls = "strong" if score >= 70 else "mid" if score >= 50 else "weak"
        more_for, more_against = pos[3:], neg[2:]
        extra = ""
        if more_for or more_against:
            extra = ("<details><summary>Rest of the evidence</summary><div class=reasons>"
                     f"<ul>{''.join(f'<li>{esc(x)}</li>' for x in more_for)}</ul>"
                     f"<ul>{''.join(f'<li>{esc(x)}</li>' for x in more_against)}</ul></div></details>")
        out.append(f"""<article class=dcard>
  <header>
    <span class=rank>{rank}</span>
    <div class=who><div class=sym>{r['ticker']}</div><div class=name>{esc(name)}</div></div>
    <div class='move up'>{fnum(r['gap_pct'], 1, pct=True, sign=True)}</div>
  </header>
  <div class=decide>
    <div><span>Score</span><b class='grade {gcls}'>{score} {grade}</b></div>
    <div><span>Trigger</span><b>{fnum(trigger, dollar=True)}</b></div>
    <div><span>Invalid below</span><b>{fnum(stop, dollar=True)}</b></div>
    <div><span>Risk</span><b>{fnum(risk_pct, 1, pct=True)}</b></div>
  </div>
  <div class=meta>{fnum(r['price'], dollar=True)} · cap {cap_html(mcap)} · prev {fnum(r['prev_close'], dollar=True)}</div>
  <div class=reasons>
    <div><div class=section-label>For</div><ul>{''.join(f'<li>{esc(x)}</li>' for x in pos[:3]) or '<li>None flagged</li>'}</ul></div>
    <div><div class=section-label>Against</div><ul>{''.join(f'<li>{esc(x)}</li>' for x in neg[:2]) or '<li>None flagged</li>'}</ul></div>
  </div>
  {extra}
  {broker_button(r['ticker'], broker)}
</article>""")
    return "<div class=deck>" + "".join(out) + "</div>"


def fetch_market_caps() -> dict[str, dict]:
    """Market cap (at prior close) and name for ~7,000 U.S. listings in one Nasdaq call."""
    try:
        r = requests.get("https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&download=true",
                         headers=UA, timeout=30)
        rows = r.json()["data"]["rows"]
    except Exception as e:
        print(f"  market caps unavailable: {e}")
        return {}
    out = {}
    for row in rows:
        try:
            cap = float(row.get("marketCap") or 0)
        except ValueError:
            cap = 0
        out[row["symbol"].replace("/", "-")] = {
            "mcap": cap or None,
            "name": re.sub(r"\s+(Common Stock|Common Shares|Class [A-Z] .*|Ordinary Shares.*|American Depositary.*)$",
                           "", (row.get("name") or "").strip())}
    return out


def cap_tier(mcap) -> tuple[str, str]:
    """(label, css class) for a market cap."""
    if not mcap:
        return "Unknown", "stale"
    if mcap >= 200e9:
        return "Mega", ""
    if mcap >= 10e9:
        return "Large", ""
    if mcap >= 2e9:
        return "Mid", ""
    if mcap >= 300e6:
        return "Small", "stale"
    return "Micro", "down"


def cap_html(mcap) -> str:
    label, c = cap_tier(mcap)
    return f"<span class='{c}'>${fbig(mcap)} · {label}</span>" if mcap else "<span class=stale>Unknown</span>"


def nasdaq_calendar(kind: str, day: date) -> list[dict]:
    url = f"https://api.nasdaq.com/api/calendar/{kind}?date={day.isoformat()}"
    try:
        r = requests.get(url, headers=UA, timeout=15)
        data = r.json().get("data") or {}
        return data.get("rows") or []
    except Exception as e:
        print(f"  nasdaq {kind} calendar unavailable: {e}")
        return []


def parse_mcap(s) -> float:
    try:
        return float(str(s).replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- analysis text

def setup_notes(r) -> list[str]:
    notes = []
    p = r["price"]
    if r["sma200"]:
        notes.append(("Above" if p > r["sma200"] else "Below") + f" 200-day avg ({fnum(r['sma200'])})")
    notes.append(("Above" if p > r["sma50"] else "Below") + f" 50-day avg ({fnum(r['sma50'])})")
    if r["rsi"] >= 70:
        notes.append(f"RSI {r['rsi']:.0f} into the gap: already overbought")
    elif r["rsi"] <= 30:
        notes.append(f"RSI {r['rsi']:.0f} into the gap: already oversold")
    if p > r["hi52"]:
        notes.append(f"Trading above 52-week high ({fnum(r['hi52'])}): breakout attempt")
    elif p >= r["hi52"] * 0.97:
        notes.append(f"Within 3% of 52-week high ({fnum(r['hi52'])})")
    if p < r["lo52"]:
        notes.append(f"Trading below 52-week low ({fnum(r['lo52'])}): breakdown")
    elif p <= r["lo52"] * 1.03:
        notes.append(f"Within 3% of 52-week low ({fnum(r['lo52'])})")
    if r["ret20"] is not None and abs(r["ret20"]) > 20:
        notes.append(f"Already {fnum(r['ret20'], 0, pct=True, sign=True)} over the prior 20 sessions")
    if r["gap_pct"] > 0 and p > r["prev_hi"]:
        notes.append(f"Gapping above yesterday's high ({fnum(r['prev_hi'])})")
    if r["gap_pct"] < 0 and p < r["prev_lo"]:
        notes.append(f"Gapping below yesterday's low ({fnum(r['prev_lo'])})")
    return notes


def scenarios(r) -> list[str]:
    hi = r["pm_hi"] or max(r["price"], r["prev_hi"])
    lo = r["pm_lo"] or min(r["price"], r["prev_lo"])
    pc = r["prev_close"]
    if r["gap_pct"] >= 0:
        return [f"Holds above {fnum(hi)} (pre-market high) on rising volume → gap extension scenario strengthens",
                f"Loses {fnum(lo)} (pre-market low) → gap-fade risk; {fnum(pc)} (prior close) is the full-fill level",
                f"Between {fnum(lo)} and {fnum(hi)} → news being absorbed, no confirmation"]
    return [f"Stays below {fnum(lo)} (pre-market low) on rising volume → breakdown scenario strengthens",
            f"Reclaims {fnum(hi)} (pre-market high) → selling may be exhausting; {fnum(pc)} (prior close) is the full-fill level",
            f"Between {fnum(lo)} and {fnum(hi)} → news being absorbed, no confirmation"]


# ---------------------------------------------------------------- HTML

CSS = """
:root{
  --bg:oklch(0.972 0.008 85);--card:oklch(0.994 0.004 85);--ink:oklch(0.28 0.02 65);
  --mute:oklch(0.48 0.02 70);--line:oklch(0.90 0.012 85);--chip:oklch(0.945 0.012 85);
  --up:oklch(0.46 0.11 155);--down:oklch(0.50 0.13 28);--accent:oklch(0.44 0.07 55);
  --warn:oklch(0.52 0.11 75);--up-bg:oklch(0.95 0.03 155);--down-bg:oklch(0.95 0.03 28);
  --bar:3.6rem
}
:root[data-theme=dark]{
  --bg:oklch(0.21 0.012 70);--card:oklch(0.25 0.012 70);--ink:oklch(0.94 0.01 85);
  --mute:oklch(0.74 0.015 80);--line:oklch(0.34 0.012 70);--chip:oklch(0.29 0.012 70);
  --up:oklch(0.78 0.11 155);--down:oklch(0.76 0.11 28);--accent:oklch(0.78 0.06 70);
  --warn:oklch(0.82 0.09 80);--up-bg:oklch(0.32 0.04 155);--down-bg:oklch(0.32 0.04 28)
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme=light]){
    --bg:oklch(0.21 0.012 70);--card:oklch(0.25 0.012 70);--ink:oklch(0.94 0.01 85);
    --mute:oklch(0.74 0.015 80);--line:oklch(0.34 0.012 70);--chip:oklch(0.29 0.012 70);
    --up:oklch(0.78 0.11 155);--down:oklch(0.76 0.11 28);--accent:oklch(0.78 0.06 70);
    --warn:oklch(0.82 0.09 80);--up-bg:oklch(0.32 0.04 155);--down-bg:oklch(0.32 0.04 28)
  }
}
*{box-sizing:border-box}
html{scroll-padding-top:4.6rem}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 20px 72px}
.topbar{position:sticky;top:0;z-index:4;background:var(--bg);border-bottom:1px solid var(--line);
margin:0 -20px;padding:10px 20px 0}
.toprow{display:flex;justify-content:space-between;align-items:flex-end;gap:16px}
h1{font-size:1.35rem;line-height:1.15;margin:0;font-weight:680;letter-spacing:-0.02em}
.kicker{margin:0 0 2px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.status{display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:flex-end;color:var(--mute);font-size:12px}
.jump{display:flex;gap:6px;overflow-x:auto;padding:8px 0 10px}
.jump a{flex:0 0 auto;color:var(--ink);background:var(--chip);border-radius:99px;padding:4px 10px;font-size:12px}
.jump a:hover{background:var(--line)}
.theme{font:inherit;font-size:12px;color:var(--ink);background:var(--card);border:1px solid var(--line);
border-radius:99px;padding:4px 10px;cursor:pointer}
.theme:hover,a.theme:hover{background:var(--chip);text-decoration:none}
a.theme{text-decoration:none}
h2{font-size:1.05rem;margin:36px 0 8px;font-weight:680;letter-spacing:-0.01em}
h3{margin:0;font-size:15px;font-weight:650}
.lede,.meta{color:var(--mute);font-size:13px;max-width:68ch}
.pill{display:inline-block;padding:2px 8px;border-radius:99px;background:var(--chip);font-size:12px;margin-right:6px}
.pill.up{background:var(--up-bg);color:var(--up)}
.pill.down{background:var(--down-bg);color:var(--down)}
.banner{background:oklch(0.95 0.04 80);color:var(--warn);border-radius:10px;padding:10px 12px;margin-top:14px}
:root[data-theme=dark] .banner{background:oklch(0.32 0.04 80)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) .banner{background:oklch(0.32 0.04 80)}}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.setup{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.setup-top{display:grid;grid-template-columns:1.6rem minmax(0,1fr) auto auto;gap:8px 14px;align-items:center}
.rank{color:var(--mute);font-variant-numeric:tabular-nums;font-size:13px}
.sym{font-weight:700;letter-spacing:.02em}
.name{color:var(--mute);font-size:12px}
.move{font-size:1.15rem;font-weight:700;font-variant-numeric:tabular-nums;text-align:right}
.score{min-width:6.5rem;text-align:right}
.score strong{font-size:1.15rem;font-variant-numeric:tabular-nums}
.score span{display:block;color:var(--mute);font-size:11px}
.track{display:block;height:4px;border-radius:99px;background:var(--line);margin-top:6px;overflow:hidden;width:6.5rem}
.score .track{margin-left:auto}
td .track{margin-left:0}
.track>i{display:block;height:100%;background:var(--accent);border-radius:inherit}
.levels{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0 4px}
.levels div{background:var(--chip);border-radius:8px;padding:8px 10px}
.levels dt{font-size:11px;color:var(--mute)}
.levels dd{margin:2px 0 0;font-weight:650;font-variant-numeric:tabular-nums}
.split{display:grid;grid-template-columns:1fr 1fr;gap:8px 18px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:11px;color:var(--mute);font-weight:650;letter-spacing:.03em;text-transform:uppercase}
th:first-child,td:first-child{text-align:left}
td.l,th.l{text-align:left;white-space:normal}
tbody tr:hover td{background:var(--chip)}
.scroll{overflow-x:auto}
.scroll thead th{position:sticky;top:0;background:var(--card)}
.deck{display:grid;gap:12px;grid-template-columns:repeat(2,minmax(0,1fr))}
.dcard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 14px 12px;display:flex;flex-direction:column;gap:10px}
.dcard header{display:flex;align-items:flex-start;gap:10px}
.dcard header .who{min-width:0;flex:1}
.decide{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px}
.decide div{background:var(--chip);border-radius:8px;padding:7px 8px}
.decide span{display:block;font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--mute)}
.decide b{display:block;margin-top:2px;font-variant-numeric:tabular-nums;font-size:13px}
.act{display:flex}
.act a{display:block;flex:1;text-align:center;background:var(--ink);color:var(--bg);border-radius:8px;
padding:9px 10px;font-size:13px;font-weight:650}
.act a:hover{text-decoration:none}
.grade.weak{color:var(--warn)}.grade.mid{color:var(--ink)}.grade.strong{color:var(--up)}
.reasons{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.reasons ul{margin:0}
.mosaic{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(148px,1fr))}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.tile .sym{font-size:14px}
.tile .move{font-size:1.05rem;text-align:left;margin-top:4px}
.groupname{font-size:12px;font-weight:650;color:var(--mute);margin:14px 0 8px}
.kv{display:grid;grid-template-columns:9.5rem 1fr;gap:3px 12px;font-size:13px;margin:8px 0}
.kv span:nth-child(odd){color:var(--mute)}
ul{margin:6px 0;padding-left:18px}li{margin:3px 0}
.heads{list-style:none;margin:0;padding:0}
.heads li{display:grid;grid-template-columns:7.2rem minmax(0,1fr);gap:12px;padding:8px 0;border-bottom:1px solid var(--line)}
.heads time{color:var(--mute);font-size:12px;font-variant-numeric:tabular-nums}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.big{font-size:1.15rem;font-weight:700;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.stale{color:var(--warn)}.warn{color:var(--warn)}
.sub{font-size:12px;color:var(--mute)}
.section-label{font-size:11px;font-weight:650;color:var(--mute);margin:12px 0 4px;letter-spacing:.05em;text-transform:uppercase}
.links{margin-top:8px}
details{margin-top:8px}
summary{cursor:pointer;color:var(--mute);font-size:13px}
details p,details li{max-width:72ch}
@media (max-width:720px){
  .wrap{padding:0 12px 56px}
  .topbar{margin:0 -12px;padding:10px 12px 0}
  .toprow{flex-direction:column;align-items:flex-start}
  .status{justify-content:flex-start}
  .setup-top{grid-template-columns:1.4rem minmax(0,1fr) auto}
  .setup-top>.move{grid-column:3;grid-row:1}
  .setup-top>.score{grid-column:2 / -1;grid-row:2;text-align:left}
  .score .track{margin-left:0}
  .deck,.decide,.reasons,.levels,.split,.heads li{grid-template-columns:1fr}
  .decide{grid-template-columns:1fr 1fr}
  .kv{grid-template-columns:1fr}
}
"""


def macro_html(macro: dict, today: date) -> str:
    parts = []
    for grp, rows in macro.items():
        tiles = []
        for r in rows:
            if r["last"] is None:
                tiles.append(f"<div class=tile><div class=name>{esc(r['label'])}</div><div class='sub stale'>Unavailable</div></div>")
                continue
            last = fnum(r["last"], 3 if r.get("yield") else 2) + ("%" if r.get("yield") else "")
            chg = fnum(r["chg"], 0 if r["unit"] == "bp" else 2, sign=True) + (" bp" if r["unit"] == "bp" else "%")
            stale = "" if r["asof"] == today.strftime("%b %d") else " stale"
            roll = " <span class=stale title='Likely contract roll; verify'>roll?</span>" if r.get("roll") else ""
            tone = cls(r["chg"])
            tiles.append(
                f"<div class=tile><div class=name>{esc(r['label'])}</div>"
                f"<div class=sym>{last}</div>"
                f"<div class='move {tone}'>{chg}{roll}</div>"
                f"<div class='sub{stale}'>{esc(r['asof'])}</div></div>")
        parts.append(f"<div class=groupname>{esc(grp)}</div><div class=mosaic>{''.join(tiles)}</div>")
    return "".join(parts)


def sectors_html(sec: pd.DataFrame) -> str:
    if sec.empty:
        return "<p class=warn>Sector ETF data unavailable.</p>"
    names = dict((s, n) for n, s in SECTOR_ETFS)
    sec = sec.sort_values("gap_pct", ascending=False)
    tiles = []
    for r in sec.itertuples():
        tone = cls(r.gap_pct)
        tiles.append(
            f"<div class=tile><div class=name>{esc(names.get(r.ticker, r.ticker))}</div>"
            f"<div class=sym>{r.ticker}</div>"
            f"<div class='move {tone}'>{fnum(r.gap_pct, 2, pct=True, sign=True)}</div>"
            f"<div class=sub>{fnum(r.gap_atr, 1, sign=True)}× ATR</div></div>")
    return f"<div class=mosaic>{''.join(tiles)}</div>"


def movers_table(df: pd.DataFrame) -> str:
    tiles = []
    for r in df.itertuples():
        tone = cls(r.gap_pct)
        mcap = None if pd.isna(r.mcap) else r.mcap
        tiles.append(f"""<article class=tile>
  <div class=sym>{r.ticker}</div>
  <div class='move {tone}'>{fnum(r.gap_pct, 1, pct=True, sign=True)}</div>
  <div class=sub>{fnum(r.gap_atr, 1, sign=True)}× ATR · {fnum(r.price, dollar=True)}</div>
  <div class=sub>Cap {cap_html(mcap)} · RSI {fnum(r.rsi, 0)}</div>
  <div class=sub>PM {fnum(r.pm_lo)}–{fnum(r.pm_hi)} · prev {fnum(r.prev_close, dollar=True)}</div>
</article>""")
    return f"<div class=mosaic>{''.join(tiles)}</div>"


def card_html(r, e: dict, today: date) -> str:
    info = e["info"]
    name = info.get("shortName") or info.get("longName") or r["name"]
    mcap = None if pd.isna(r["mcap"]) else r["mcap"]
    flt = info.get("floatShares")
    direction = "Gap up" if r["gap_pct"] > 0 else "Gap down"
    news = "".join(
        f"<li><a href='{esc(n['url'])}' target=_blank>{esc(n['title'])}</a> "
        f"<span class=sub>· {esc(n['provider'])} · {n['ts']:%b %d %H:%M} ET</span></li>" for n in e["news"])
    news = f"<ul>{news}</ul>" if news else \
        (f"<p class=warn>No headline in the last {NEWS_MAX_AGE_H}h (Yahoo/Google News). "
         "Catalyst unverified: check SEC filings / IR.</p>")
    grades = "<ul>" + "".join(f"<li>{esc(g)}</li>" for g in e["grades"]) + "</ul>" if e["grades"] else \
        "<p class=sub>No analyst rating changes in the last 3 days (Yahoo).</p>"
    earn = f"<span class=pill>Earnings {e['earnings']:%b %d}</span>" if e["earnings"] else ""
    short = info.get("shortPercentOfFloat")
    fund = [
        ("Sector", f"{info.get('sector', '—')} / {info.get('industry', '—')}"),
        ("Market cap", cap_html(mcap or info.get("marketCap"))),
        ("Float", (fbig(flt) + " sh" + (" <span class=down>(low float)</span>" if flt < LOW_FLOAT else ""))
         if flt else "Unavailable"),
        ("Forward P/E", fnum(info.get("forwardPE"), 1)),
        ("EV/EBITDA", fnum(info.get("enterpriseToEbitda"), 1)),
        ("Rev growth (yoy)", fnum((info.get("revenueGrowth") or float("nan")) * 100, 1, pct=True)),
        ("Short % float", fnum(short * 100, 1, pct=True) + " <span class=sub>(bi-monthly, delayed)</span>"
         if short else "Unavailable"),
        ("Days to cover", fnum(info.get("shortRatio"), 1)),
    ]
    kv = "".join(f"<span>{k}</span><span>{v}</span>" for k, v in fund)
    tech = "".join(f"<li>{esc(n)}</li>" for n in setup_notes(r))
    scen = "".join(f"<li>{esc(s)}</li>" for s in scenarios(r))
    tone = "up" if r["gap_pct"] > 0 else "down"
    first = e["news"][:1]
    lead = (f"<a href='{esc(first[0]['url'])}' target=_blank>{esc(first[0]['title'])}</a>"
            f"<div class=sub>{esc(first[0]['provider'])} · {first[0]['ts']:%b %d %H:%M} ET</div>") if first else \
        f"<p class=warn>No headline in the last {NEWS_MAX_AGE_H}h. Catalyst unverified.</p>"
    return f"""<article class=dcard>
  <header>
    <div class=who><div class=sym>{r['ticker']}</div><div class=name>{esc(name)}</div></div>
    <div class='move {tone}'>{fnum(r['gap_pct'], 1, pct=True, sign=True)}</div>
  </header>
  <div class=decide>
    <div><span>Price</span><b>{fnum(r['price'], dollar=True)}</b></div>
    <div><span>vs range</span><b>{fnum(r['gap_atr'], 1, sign=True)}×</b></div>
    <div><span>PM high</span><b>{fnum(r['pm_hi'], dollar=True)}</b></div>
    <div><span>PM low</span><b>{fnum(r['pm_lo'], dollar=True)}</b></div>
  </div>
  <div class=meta><span class='pill {tone}'>{direction}</span>{earn}<span class=pill>Cap {cap_html(mcap)}</span></div>
  <div>{lead}</div>
  <details>
    <summary>Context, scenarios, fundamentals</summary>
    <div class=section-label>Headlines</div>{news}
    <div class=section-label>Analyst actions</div>{grades}
    <div class=section-label>Technical context</div><ul>{tech}</ul>
    <div class=section-label>Conditional scenarios</div><ul>{scen}</ul>
    <div class=section-label>Fundamentals</div><div class=kv>{kv}</div>
    <div class='sub links'>
      <a target=_blank href='https://finance.yahoo.com/quote/{r['ticker']}'>Yahoo</a> ·
      <a target=_blank href='https://finviz.com/quote.ashx?t={r['ticker']}'>Finviz</a> ·
      <a target=_blank href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={r['ticker']}&type=&dateb=&owner=include&count=40'>SEC</a> ·
      <a target=_blank href='https://www.barchart.com/stocks/quotes/{r['ticker']}/options'>Options</a>
    </div>
  </details>
</article>"""


def calendars_html(earn_rows, econ_rows, universe: set) -> str:
    def et_time(row):
        t = row.get("time", "")
        return {"time-pre-market": "Before open", "time-after-hours": "After close"}.get(t, "Time n/a")

    big = sorted(earn_rows, key=lambda r: parse_mcap(r.get("marketCap")), reverse=True)
    big = [r for r in big if parse_mcap(r.get("marketCap")) >= 2e9 or r.get("symbol") in universe][:30]
    if big:
        tiles = "".join(
            f"<div class=tile><div class=sym>{esc(r.get('symbol'))}</div>"
            f"<div class=name>{esc(r.get('name'))}</div>"
            f"<div class=sub>{et_time(r)} · EPS {esc(r.get('epsForecast') or '—')}</div>"
            f"<div class=sub>{esc(r.get('marketCap') or '—')}</div></div>" for r in big)
        earn = (f"<div class=groupname>Earnings today, market cap at least $2B</div>"
                f"<div class=mosaic>{tiles}</div>")
    else:
        earn = ("<div class=card><div class=section-label>Earnings today</div><p class=warn>Unavailable. "
                "See <a target=_blank href='https://www.nasdaq.com/market-activity/earnings'>Nasdaq earnings</a>."
                "</p></div>")
    us = [r for r in econ_rows if "united states" in str(r.get("country", "")).lower()]

    def cell(v):
        v = html.unescape(str(v or "")).replace("\xa0", " ").strip()
        return esc(v) if v else "—"

    if us:
        trs = "".join(f"<tr><td>{cell(r.get('gmt'))}</td><td class=l>{cell(r.get('eventName'))}</td>"
                      f"<td>{cell(r.get('consensus'))}</td><td>{cell(r.get('previous'))}</td>"
                      f"<td>{cell(r.get('actual'))}</td></tr>" for r in us)
        econ = (f"<div class='card scroll'><div class=section-label>U.S. economic calendar (Nasdaq feed, "
                f"often incomplete; time zone unverified. Cross-check Investing.com)</div>"
                f"<table><tr><th>Time</th><th class=l>Event</th><th>Consensus</th><th>Prior</th><th>Actual</th></tr>"
                f"{trs}</table></div>")
    else:
        econ = ("<div class=card><div class=section-label>U.S. economic calendar</div><p class=warn>Unavailable.</p>"
                "</div>")
    links = """<div class=card><div class=section-label>Verify / go deeper</div><ul>
<li><a target=_blank href='https://www.investing.com/economic-calendar/'>Investing.com economic calendar</a></li>
<li><a target=_blank href='https://www.federalreserve.gov/newsevents/calendar.htm'>Fed calendar (speakers)</a></li>
<li><a target=_blank href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K'>Latest SEC 8-K filings</a></li>
<li><a target=_blank href='https://www.fda.gov/news-events/fda-newsroom/press-announcements'>FDA press announcements</a></li>
<li><a target=_blank href='https://www.cnbc.com/pre-markets/'>CNBC pre-markets</a> · <a target=_blank href='https://www.benzinga.com/premarket'>Benzinga pre-market</a></li>
<li><a target=_blank href='https://finviz.com/news.ashx'>Finviz news</a> · <a target=_blank href='https://www.barchart.com/options/unusual-activity/stocks'>Unusual options (Barchart)</a></li>
</ul></div>"""
    return f"<div class=grid>{earn}{econ}{links}</div>"


def build_page(ctx: dict, refresh_min: int | None) -> str:
    refresh = f"<meta http-equiv=refresh content='{refresh_min * 60}'>" if refresh_min else ""
    fresh = (f"Refreshes every {refresh_min} min" if refresh_min else "Static snapshot")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>{refresh}
<title>CLaude Premarket Screener · {ctx['built']:%b %d}</title><style>{CSS}</style></head><body><div class=wrap>
<header class=topbar>
  <div class=toprow>
    <div><p class=kicker>Research screen</p><h1>CLaude Premarket Screener</h1></div>
    <div class=status>
      <span class=pill>{esc(ctx['state'])}</span>
      <span>{ctx['built']:%a %b %d · %H:%M} ET</span>
      <span>{ctx['n_scanned']} names</span>
      <span>{fresh}</span>
      <button type=button class=theme id=theme>Theme</button>
    </div>
  </div>
  <nav class=jump>
    <a href=#picks>Setups</a><a href=#tape>Tape</a><a href=#sectors>Sectors</a>
    <a href=#headlines>Headlines</a><a href=#movers>Movers</a><a href=#evidence>Evidence</a>
    <a href=#calendar>Calendar</a><a href=#record>Record</a>
  </nav>
</header>
{ctx['warning']}
<h2 id=picks>Today's long setups</h2>
<p class=lede>Ranked by evidence, not certainty. A setup is the trigger: price holds above the pre-market high after the open, with volume. Names under ${fbig(MIN_PICK_MCAP)} market cap are excluded. Not investment advice.</p>
{ctx['picks']}
<h2 id=tape>Market tape</h2>{ctx['macro']}
<h2 id=sectors>Sector and factor tape</h2>{ctx['sectors']}
<h2 id=headlines>Headlines outside the index scan</h2>
<p class=lede>Google News, last 20 hours. This is where small-cap readouts and deals show up.</p>
{ctx['headlines']}
<h2 id=movers>Unusual movers</h2>
<p class=lede>Top {TOP_TABLE} by how far the move is versus a normal day (ATR). 2× ATR means twice a typical full-day range. Yahoo pre-market volume is partial.</p>
{ctx['table']}
<h2 id=evidence>Evidence on the largest moves</h2>
<p class=lede>Ordered by how unusual the move is, not by how attractive it looks. Headlines are candidate catalysts.</p>
<details><summary>Questions to ask of each name</summary>
<ul><li>What new information arrived, and did it land before or after the move?</li>
<li>Why would it change cash flows, valuation, or positioning, and how much is already in the price?</li>
<li>What would invalidate the move?</li></ul></details>
<div class=deck>{ctx['cards']}</div>
<h2 id=calendar>What could change the picture</h2>{ctx['calendars']}
<h2 id=record>How previous setups did</h2>
<details><summary>How a setup is graded</summary>
<p>Each setup is replayed on 5-minute bars. Entry is when price trades above the trigger. Exit is invalidation or the close. Result is in R, where 1R is the distance from entry to invalidation. Weights take a small step toward what worked, and a larger step when a call was completely wrong.</p>
</details>
{ctx['learning']}
<details><summary>Data limitations</summary><ul>
<li>Source: Yahoo Finance via yfinance. Quotes can be delayed up to 15 minutes, and bars can be missing.</li>
<li>Not in this screen: live options flow, implied volatility, gamma, borrow cost, order flow, or VWAP before the open.</li>
<li>Short interest is Yahoo's bi-monthly figure and lags by 2 or more weeks.</li>
<li>News is Yahoo's feed from the last {NEWS_MAX_AGE_H} hours. A missing headline means unverified, not "no catalyst".</li>
<li>Amber figures have a last bar from a prior day.</li>
<li>Universe: S&amp;P 500, S&amp;P 400, Nasdaq-100, and watchlist.txt. Add tickers to watchlist.txt to include them.</li>
<li>Yahoo often reports pre-market volume as 0, so those columns may be blank.</li>
</ul></details>
</div>
<script>
const root=document.documentElement, btn=document.getElementById('theme');
const saved=localStorage.getItem('pm-theme');
if(saved) root.dataset.theme=saved;
function label(){{
  const dark=root.dataset.theme==='dark'||(!root.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches);
  btn.textContent=dark?'Light':'Dark';
}}
label();
btn.addEventListener('click',()=>{{
  const dark=root.dataset.theme==='dark'||(!root.dataset.theme&&matchMedia('(prefers-color-scheme: dark)').matches);
  root.dataset.theme=dark?'light':'dark';
  localStorage.setItem('pm-theme', root.dataset.theme);
  label();
}});
</script>
</body></html>"""


# ---------------------------------------------------------------- main

def build(tickers: list[str], daily: pd.DataFrame, refresh_min: int | None, learn_summary: dict) -> Path:
    t0 = now_et()
    today = t0.date()
    print(f"[{t0:%H:%M:%S}] macro...")
    macro = fetch_macro()
    etfs = [s for _, s in SECTOR_ETFS]
    print(f"[{now_et():%H:%M:%S}] intraday bars for {len(tickers)} tickers...")
    intraday = fetch_intraday(tickers)
    m = compute_metrics(tickers, daily, intraday, today)

    warning = ""
    if m.empty:
        warning = ("<p class=banner><b>No trades yet today in the data feed.</b> Possibly a market holiday, "
                   "too early (before 4:00 ET), or a data outage.</p>")
        m = pd.DataFrame(columns=["ticker", "gap_pct", "gap_atr", "dollar_vol", "price"])
    caps = fetch_market_caps()
    # Scale the prior-close cap by today's move so it reflects the current price.
    m["mcap"] = [(caps.get(t, {}).get("mcap") or float("nan")) * (p / pc)
                 for t, p, pc in zip(m["ticker"], m["price"], m.get("prev_close", m["price"]))]
    m["name"] = [caps.get(t, {}).get("name") or t for t in m["ticker"]]
    sec = m[m["ticker"].isin(etfs)]
    stocks = m[~m["ticker"].isin(etfs)]
    liquid = stocks[(stocks["price"] >= MIN_PRICE) & (stocks["dollar_vol"] >= MIN_DOLLAR_VOL)]
    movers = liquid[liquid["gap_pct"].abs() >= MIN_ABS_GAP_PCT].copy()
    movers["score"] = movers["gap_atr"].abs()
    movers = movers.sort_values("score", ascending=False)

    cache: dict[str, dict] = {}

    def get_e(t):
        if t not in cache:
            print(f"  enriching {t}")
            cache[t] = enrich(t, today)
        return cache[t]

    cards = [card_html(r, get_e(r["ticker"]), today) for _, r in movers.head(TOP_CARDS).iterrows()]

    sector_moves = dict(zip(sec["ticker"], sec["gap_pct"]))
    es = next((x for x in macro["U.S. futures"] if x["sym"] == "ES=F"), None)
    market_chg = es["chg"] if es else None
    gappers = movers[movers["gap_pct"] > 0].sort_values("gap_atr", ascending=False).head(20)
    weights = learning.load_weights()
    scored = []
    for _, r in gappers.iterrows():
        e = get_e(r["ticker"])
        score, pos, neg, feats = long_score(r, e, sector_moves, market_chg, weights)
        scored.append((score, r, e, pos, neg, feats))
    scored.sort(key=lambda x: x[0], reverse=True)
    journal = []
    for score, r, _, _, _, feats in scored:
        trig, stop = levels(r)
        if trig > stop:
            journal.append({"ticker": r["ticker"], "score": score, "price": float(r["price"]),
                            "trigger": trig, "stop": stop,
                            "mcap": None if pd.isna(r["mcap"]) else float(r["mcap"]), "features": feats})
    learning.record_picks(today, t0, journal)
    big_enough = [not pd.isna(x[1]["mcap"]) and x[1]["mcap"] >= MIN_PICK_MCAP for x in scored]
    eligible = [x for x, ok in zip(scored, big_enough) if ok]
    excluded = [x for x, ok in zip(scored, big_enough) if not ok]
    broker = broker_config()
    picks = picks_html(eligible[:5], broker)
    if excluded:
        picks += ("<p class=sub>Excluded as pump-and-dump risk (market cap under "
                  f"${fbig(MIN_PICK_MCAP)} or unknown): " + ", ".join(
                      f"{x[1]['ticker']} ({'$' + fbig(x[1]['mcap']) if not pd.isna(x[1]['mcap']) else 'cap unknown'}, "
                      f"{x[1]['gap_pct']:+.1f}%)" for x in excluded) + "</p>")
    if not cards:
        cards = ["<div class=card><p>No liquid stock is moving ≥1% vs its prior close yet.</p></div>"]

    heads = market_headlines()
    heads_html = ("<div class=card><ul class=heads>" + "".join(
        f"<li><time>{n['ts']:%b %d %H:%M}</time><div><a href='{esc(n['url'])}' target=_blank>{esc(n['title'])}</a> "
        f"<div class=sub>{esc(n['provider'])}</div></div></li>" for n in heads)
        + "</ul></div>") if heads else "<p class=banner>Market headlines unavailable.</p>"

    print(f"[{now_et():%H:%M:%S}] calendars...")
    cal = calendars_html(nasdaq_calendar("earnings", today), nasdaq_calendar("economicevents", today),
                         set(tickers))

    page = build_page({
        "state": session_state(t0), "built": now_et(), "n_scanned": len(liquid), "warning": warning,
        "macro": macro_html(macro, today), "sectors": sectors_html(sec),
        "table": movers_table(movers.head(TOP_TABLE)), "cards": "".join(cards), "calendars": cal,
        "headlines": heads_html, "picks": picks,
        "learning": learning.report_html(learn_summary, esc, fnum),
    }, refresh_min)
    REPORTS.mkdir(exist_ok=True)
    dated = REPORTS / f"premarket_{today.isoformat()}.html"
    dated.write_text(page, encoding="utf-8")
    latest = REPORTS / "latest.html"
    latest.write_text(page, encoding="utf-8")
    print(f"[{now_et():%H:%M:%S}] wrote {dated}")
    try:
        publish.upload_dashboard(page)
    except Exception as e:
        print(f"  private app upload failed: {e}")
    return latest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, metavar="MIN", help="rebuild every MIN minutes until 09:35 ET")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    tickers = load_universe() + [s for _, s in SECTOR_ETFS]
    print(f"[{now_et():%H:%M:%S}] daily history for {len(tickers)} tickers...")
    daily = fetch_daily(tickers)

    print(f"[{now_et():%H:%M:%S}] grading previous setups and updating weights...")
    try:
        learn_summary = learning.run_learning_cycle(now_et().date(), ET)
    except Exception as e:
        print(f"  learning cycle failed: {e}")
        learn_summary = {"days": [], "changes": {}, "wrong": []}

    opened = False
    while True:
        path = build(tickers, daily, args.watch, learn_summary)
        if not args.no_open and not opened:
            webbrowser.open(path.as_uri())
            opened = True
        t = now_et()
        if not args.watch or (t.hour * 60 + t.minute) >= 9 * 60 + 35:
            break
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    main()
