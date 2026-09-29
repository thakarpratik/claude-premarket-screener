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
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

import learning

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
               weights: dict) -> tuple[int, list[str], list[str], dict]:
    """0-100 score for a gap-up long setup: weighted sum of features (weights are learned daily).

    Returns (score, positives, negatives, features)."""
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
    else:
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
    if g > 5 and not specific:
        f["pump_pattern"] = 1
        neg.append(f"{g:.0f}× normal range with no company news: classic pump pattern")
    if r["ret20"] is not None and r["ret20"] > 100:
        f["parabolic"] = 1
        neg.append(f"Up {r['ret20']:.0f}% in 20 sessions before today: parabolic")
    return learning.score_from(f, weights), pos, neg, f


def levels(r) -> tuple[float, float]:
    """(trigger, invalidation) for a long setup."""
    trigger = r["pm_hi"] or r["price"]
    stop = min(r["pm_lo"] or r["prev_close"], r["price"] * 0.99)
    return float(trigger), float(stop)


def picks_html(picks: list) -> str:
    if not picks:
        return "<div class=card><p>No liquid stock is gapping up ≥1% with enough evidence to score yet.</p></div>"
    out = []
    for rank, (score, r, e, pos, neg, _) in enumerate(picks, 1):
        name = e["info"].get("shortName") or r["name"]
        mcap = None if pd.isna(r["mcap"]) else r["mcap"]
        trigger, stop = levels(r)
        risk_pct = (trigger - stop) / trigger * 100
        grade = "Strong" if score >= 70 else "Moderate" if score >= 50 else "Weak"
        out.append(f"""<div class=card>
  <div style='display:flex;justify-content:space-between;align-items:baseline;gap:8px'>
    <h3>#{rank} {esc(name)} <span class=sub>{r['ticker']}</span></h3>
    <span class=big>{score}<span class=sub>/100 · {grade}</span></span></div>
  <div class=meta><span class=pill>Mkt cap {cap_html(mcap)}</span>{fnum(r['price'], dollar=True)} · <span class=up>{fnum(r['gap_pct'], 1, pct=True, sign=True)}</span>
    vs prev close {fnum(r['prev_close'], dollar=True)}</div>
  <div class=kv><span>Long trigger</span><span>Holds above <b>{fnum(trigger, dollar=True)}</b> (pre-market high) after the open, with volume</span>
    <span>Invalidation</span><span>Below <b>{fnum(stop, dollar=True)}</b> (pre-market low). Setup is broken.</span>
    <span>Risk to invalidation</span><span>{fnum(risk_pct, 1, pct=True)} from trigger</span></div>
  <div class=section-label>Evidence for</div><ul>{''.join(f'<li>{esc(x)}</li>' for x in pos)}</ul>
  <div class=section-label>Evidence against</div><ul>{''.join(f'<li>{esc(x)}</li>' for x in neg) or '<li>None flagged</li>'}</ul>
</div>""")
    return "<div class=cards>" + "".join(out) + "</div>"


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
:root{--bg:#f7f7f5;--card:#fff;--ink:#1b1c1e;--mute:#6b6f76;--line:#e3e3df;--up:#0f7b45;--down:#c0392b;
--accent:#2f5bd3;--warn:#9a6700;--chip:#eef0f3}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#111214;--card:#1a1b1e;--ink:#e8e8e6;
--mute:#9aa0a6;--line:#2b2d31;--up:#3ecf8e;--down:#ff6b5e;--accent:#7aa2ff;--warn:#e3b341;--chip:#24262a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 -apple-system,Segoe UI,Inter,Roboto,sans-serif}
.wrap{max-width:1280px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px;text-transform:uppercase;
letter-spacing:.06em;color:var(--mute)}h3{margin:0;font-size:17px}
.meta{color:var(--mute);font-size:13px}.pill{display:inline-block;padding:2px 8px;border-radius:99px;
background:var(--chip);font-size:12px;margin-right:6px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{font-size:12px;color:var(--mute);font-weight:600}th:first-child,td:first-child{text-align:left}
td.l,th.l{text-align:left;white-space:normal}.up{color:var(--up)}.down{color:var(--down)}
.stale{color:var(--warn)}.scroll{overflow-x:auto}
.cards{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
@media (max-width:520px){.cards{grid-template-columns:1fr}}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:13px;margin:8px 0}
.kv span:nth-child(odd){color:var(--mute)}ul{margin:6px 0;padding-left:18px}li{margin:2px 0}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.big{font-size:22px;font-weight:650}.warn{color:var(--warn)}.sub{font-size:12px;color:var(--mute)}
.section-label{font-size:12px;font-weight:600;color:var(--mute);margin-top:10px;text-transform:uppercase;letter-spacing:.04em}
"""


def macro_html(macro: dict, today: date) -> str:
    parts = []
    for grp, rows in macro.items():
        trs = []
        for r in rows:
            if r["last"] is None:
                trs.append(f"<tr><td>{esc(r['label'])}</td><td colspan=3 class=stale>Unavailable</td></tr>")
                continue
            last = fnum(r["last"], 3 if r.get("yield") else 2) + ("%" if r.get("yield") else "")
            chg = fnum(r["chg"], 0 if r["unit"] == "bp" else 2, sign=True) + (" bp" if r["unit"] == "bp" else "%")
            stale = "" if r["asof"] == today.strftime("%b %d") else " stale"
            roll = " <span class=stale title='Likely contract roll; verify'>roll?</span>" if r.get("roll") else ""
            trs.append(f"<tr><td>{esc(r['label'])}</td><td>{last}</td><td class='{cls(r['chg'])}'>{chg}{roll}</td>"
                       f"<td class='sub{stale}'>{esc(r['asof'])}</td></tr>")
        parts.append(f"<div class=card><div class=section-label>{esc(grp)}</div><table>"
                     f"<tr><th>Instrument</th><th>Last</th><th>Chg</th><th>Bar</th></tr>{''.join(trs)}</table></div>")
    return "<div class=grid>" + "".join(parts) + "</div>"


def sectors_html(sec: pd.DataFrame) -> str:
    if sec.empty:
        return "<p class=warn>Sector ETF data unavailable.</p>"
    names = dict((s, n) for n, s in SECTOR_ETFS)
    sec = sec.sort_values("gap_pct", ascending=False)
    trs = "".join(f"<tr><td>{esc(names.get(r.ticker, r.ticker))} <span class=sub>{r.ticker}</span></td>"
                  f"<td class='{cls(r.gap_pct)}'>{fnum(r.gap_pct, 2, pct=True, sign=True)}</td>"
                  f"<td>{fnum(r.gap_atr, 1, sign=True)}×</td></tr>" for r in sec.itertuples())
    return (f"<div class='card scroll'><table><tr><th>Sector / factor ETF</th><th>vs prior close</th>"
            f"<th>in ATRs</th></tr>{trs}</table></div>")


def movers_table(df: pd.DataFrame) -> str:
    trs = []
    for r in df.itertuples():
        trs.append(
            f"<tr><td><b>{r.ticker}</b></td><td>{cap_html(None if pd.isna(r.mcap) else r.mcap)}</td>"
            f"<td>{fnum(r.price, dollar=True)}</td>"
            f"<td class='{cls(r.gap_pct)}'>{fnum(r.gap_pct, 2, pct=True, sign=True)}</td>"
            f"<td>{fnum(r.gap_atr, 1, sign=True)}×</td><td>{fnum(r.atr_pct, 1, pct=True)}</td>"
            f"<td>{fbig(r.pm_vol)}</td><td>{fnum(r.pm_vol_pct_adv, 1, pct=True)}</td>"
            f"<td>{fbig(r.dollar_vol)}</td><td>{fnum(r.rsi, 0)}</td>"
            f"<td>{fnum(r.pm_hi)}</td><td>{fnum(r.pm_lo)}</td><td>{fnum(r.prev_close)}</td></tr>")
    return ("<div class='card scroll'><table><tr><th>Ticker</th><th>Mkt cap</th><th>Price</th><th>Move</th><th>Move/ATR</th>"
            "<th>ATR%</th><th>PM vol</th><th>PM vol % ADV</th><th>$ ADV</th><th>RSI</th>"
            "<th>PM high</th><th>PM low</th><th>Prev close</th></tr>" + "".join(trs) + "</table></div>")


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
    return f"""<div class=card>
  <div style='display:flex;justify-content:space-between;align-items:baseline;gap:8px'>
    <h3>{esc(name)} <span class=sub>{r['ticker']}</span></h3>
    <span class='big {cls(r['gap_pct'])}'>{fnum(r['gap_pct'], 1, pct=True, sign=True)}</span></div>
  <div class=meta><span class=pill>{direction}</span>{earn}<span class=pill>{fnum(r['gap_atr'], 1, sign=True)}× ATR</span>
    <span class=pill>Mkt cap {cap_html(mcap)}</span>
    {fnum(r['price'], dollar=True)} · prev close {fnum(r['prev_close'], dollar=True)}
    · {f"PM vol {fbig(r['pm_vol'])} ({fnum(r['pm_vol_pct_adv'], 1, pct=True)} of avg day)" if r['pm_vol']
       else "PM volume unavailable from Yahoo"} · avg day {fbig(r['adv'])} sh</div>
  <div class=section-label>Fresh headlines (possible catalyst)</div>{news}
  <div class=section-label>Analyst actions</div>{grades}
  <div class=section-label>Technical context</div><ul>{tech}</ul>
  <div class=section-label>Conditional scenarios</div><ul>{scen}</ul>
  <div class=section-label>Fundamentals & positioning</div><div class=kv>{kv}</div>
  <div class=section-label>Your checklist</div>
  <ul class=sub><li>What NEW information arrived, and when? Did it land before or after the move?</li>
  <li>Why does it change cash flows, valuation or positioning? How much is already priced in?</li>
  <li>What would invalidate the move? Look for contradicting evidence.</li></ul>
  <div class=sub>Links: <a target=_blank href='https://finance.yahoo.com/quote/{r['ticker']}'>Yahoo</a> ·
    <a target=_blank href='https://finviz.com/quote.ashx?t={r['ticker']}'>Finviz</a> ·
    <a target=_blank href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={r['ticker']}&type=&dateb=&owner=include&count=40'>SEC filings</a> ·
    <a target=_blank href='https://www.barchart.com/stocks/quotes/{r['ticker']}/options'>Options</a></div>
</div>"""


def calendars_html(earn_rows, econ_rows, universe: set) -> str:
    def et_time(row):
        t = row.get("time", "")
        return {"time-pre-market": "Before open", "time-after-hours": "After close"}.get(t, "Time n/a")

    big = sorted(earn_rows, key=lambda r: parse_mcap(r.get("marketCap")), reverse=True)
    big = [r for r in big if parse_mcap(r.get("marketCap")) >= 2e9 or r.get("symbol") in universe][:30]
    if big:
        trs = "".join(f"<tr><td><b>{esc(r.get('symbol'))}</b></td><td class=l>{esc(r.get('name'))}</td>"
                      f"<td>{et_time(r)}</td><td>{esc(r.get('epsForecast') or '—')}</td>"
                      f"<td>{esc(r.get('marketCap') or '—')}</td></tr>" for r in big)
        earn = (f"<div class='card scroll'><div class=section-label>Earnings today (market cap ≥ $2B)</div><table>"
                f"<tr><th>Ticker</th><th class=l>Company</th><th>When</th><th>EPS est.</th><th>Mkt cap</th></tr>"
                f"{trs}</table></div>")
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
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content='width=device-width,initial-scale=1'>{refresh}
<title>Pre-market Screen</title><style>{CSS}</style></head><body><div class=wrap>
<h1>Pre-market catalyst screen</h1>
<div class=meta><span class=pill>{esc(ctx['state'])}</span>Built {ctx['built']:%A %b %d, %Y · %H:%M:%S} ET ·
 {ctx['n_scanned']} liquid stocks scanned (price ≥ ${MIN_PRICE:.0f}, avg $ volume ≥ {fbig(MIN_DOLLAR_VOL)}) ·
 {'auto-refresh every ' + str(refresh_min) + ' min' if refresh_min else 'static snapshot'}</div>
{ctx['warning']}
<h2>Learning: how previous setups actually did</h2>
<p class=sub>Each setup is replayed on 5-minute bars: entry when price trades above the trigger, exit at invalidation or
the close. Result is in R (1R = the distance from entry to invalidation). The weights take a small, capped step
toward whatever worked, and a larger step when a call was completely wrong.</p>
{ctx['learning']}
<h2>Today's top long setups</h2>
<p class=sub>Mechanical score (0–100) across catalyst freshness, earnings and upgrades, size of move, trend, RSI, whether the
gap is holding, sector and market confirmation, liquidity, and pump-and-dump guards (market cap, float,
parabolic runs, big moves with no news). Stocks under ${fbig(MIN_PICK_MCAP)} market cap are never picked. It ranks evidence, not certainty. Most gaps fade, so the
trigger (holding above the pre-market high after the open) matters more than the score. Not personalized investment advice.</p>
{ctx['picks']}
<h2>Market dashboard</h2>{ctx['macro']}
<h2>Sector & factor tape</h2>{ctx['sectors']}
<h2>Pre-market headlines (all caps, including small caps outside the scan)</h2>
<p class=sub>Google News, last 20h. Use this to catch movers the index-based scan misses (biotech readouts, deals, small caps).</p>
{ctx['headlines']}
<h2>Unusual movers: top {TOP_TABLE} by move relative to typical daily range (ATR)</h2>
<p class=sub>A move of 2× ATR means the stock has moved twice its normal full-day range before the open.
Pre-market volume from Yahoo is partial and delayed. Treat it as a relative signal, not an exact count.</p>
{ctx['table']}
<h2>Top {TOP_CARDS}: evidence cards</h2>
<p class=sub>Ordered by how unusual the move is, not by attractiveness. "Gap up" and "gap down" describe price action, not a
recommendation. Headlines are candidate catalysts. Confirm them against primary sources.</p>
<div class=cards>{ctx['cards']}</div>
<h2>What could change the picture today</h2>{ctx['calendars']}
<h2>Data limitations</h2><div class=card><ul class=sub>
<li>Source: Yahoo Finance via yfinance (unofficial; quotes can be delayed up to 15 min; bars can be missing).</li>
<li>Not available: live options flow, implied volatility, gamma levels, borrow cost, order flow, VWAP before the open.</li>
<li>Short interest is Yahoo's bi-monthly figure and lags by 2+ weeks.</li>
<li>News is Yahoo's aggregated feed from the last {NEWS_MAX_AGE_H}h. A missing headline means unverified, not "no catalyst".</li>
<li>Rows marked in amber in the dashboard have a last bar from a prior day (that market has not traded yet today).</li>
<li>Universe: S&amp;P 500 + S&amp;P 400 + Nasdaq-100 + watchlist.txt. Small caps and ADRs outside those lists are
not scanned. Use the headlines section, or add tickers to watchlist.txt.</li>
<li>Yahoo frequently reports pre-market volume as 0, so the PM-volume columns may be blank.</li>
<li>Research screen only, not investment advice.</li></ul></div>
</div></body></html>"""


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
        warning = ("<p class=warn><b>No trades yet today in the data feed.</b> Possibly a market holiday, "
                   "too early (before 4:00 ET) or a data outage.</p>")
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
    picks = picks_html(eligible[:5])
    if excluded:
        picks += ("<p class=sub>Excluded as pump-and-dump risk (market cap under "
                  f"${fbig(MIN_PICK_MCAP)} or unknown): " + ", ".join(
                      f"{x[1]['ticker']} ({'$' + fbig(x[1]['mcap']) if not pd.isna(x[1]['mcap']) else 'cap unknown'}, "
                      f"{x[1]['gap_pct']:+.1f}%)" for x in excluded) + "</p>")
    if not cards:
        cards = ["<div class=card><p>No liquid stock is moving ≥1% vs its prior close yet.</p></div>"]

    heads = market_headlines()
    heads_html = ("<div class=card><ul>" + "".join(
        f"<li><a href='{esc(n['url'])}' target=_blank>{esc(n['title'])}</a> "
        f"<span class=sub>· {esc(n['provider'])} · {n['ts']:%b %d %H:%M} ET</span></li>" for n in heads)
        + "</ul></div>") if heads else "<p class=warn>Market headlines unavailable.</p>"

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
