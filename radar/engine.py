"""Data pipeline: fetch -> features -> grade yesterday's calls -> retrain (daily) -> score -> cards."""
import json
import math
import pickle
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

from . import features as F
from . import pump
from . import publish
from . import store
from .config import (BIG_MOVE_PCT, HIST_CACHE, HISTORY_PERIOD, HOLDOUT_DAYS, MARKET_TICKERS, MISS_THRESHOLD,
                     MISS_WEIGHT, MODEL_PATH, MOVER_MIN_MARKET_CAP, MOVER_MIN_PRICE, MOVERS_PER_LIST, NEWS_TOP_N,
                     SECTOR_ETF, UNIVERSE)
from .model import LogReg, auc, select_and_fit

ET = ZoneInfo("America/New_York")
STATUS = {"running": False, "step": "idle", "last_refresh": None, "last_error": None}
_refresh_lock = threading.Lock()
_state = {"hist": {}, "hist_day": None, "snapshot": None, "model": None}


# ---------------------------------------------------------------- helpers
def now_et():
    return datetime.now(ET)


def session_state(t=None):
    t = t or now_et()
    if t.weekday() >= 5:
        return "closed"
    hm = t.hour * 60 + t.minute
    if 570 <= hm < 960:
        return "open"
    if 240 <= hm < 570:
        return "pre"
    if 960 <= hm < 1200:
        return "after"
    return "closed"


def _bar_is_partial(last_date):
    t = now_et()
    return last_date.date() == t.date() and (t.hour * 60 + t.minute) < 16 * 60 + 10


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else round(float(o), 4)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


def _step(msg):
    STATUS["step"] = msg
    print(f"[{now_et():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- data fetch
def _download(symbols, period):
    out = {}
    symbols = list(dict.fromkeys(symbols))
    for i in range(0, len(symbols), 80):
        chunk = symbols[i:i + 80]
        d = yf.download(chunk + (["SPY"] if len(chunk) == 1 else []), period=period, interval="1d",
                        group_by="ticker", auto_adjust=True, threads=True, progress=False)
        for s in chunk:
            try:
                sub = d[s][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(sub):
                idx = pd.to_datetime(sub.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                sub.index = idx.normalize()
                out[s] = sub
    return out


def load_history(symbols):
    hist = _state["hist"]
    if not hist and HIST_CACHE.exists():
        try:
            hist, day = pickle.loads(HIST_CACHE.read_bytes())
            _state["hist"], _state["hist_day"] = hist, day
        except Exception:
            hist = {}
    today = now_et().date().isoformat()
    missing = [s for s in symbols if s not in hist]
    if _state["hist_day"] != today:
        _step(f"Downloading {HISTORY_PERIOD} daily history for {len(symbols)} symbols")
        hist.update(_download(symbols, HISTORY_PERIOD))
        _state["hist_day"] = today
    else:
        if missing:
            _step(f"Downloading history for {len(missing)} new symbols")
            hist.update(_download(missing, HISTORY_PERIOD))
        _step("Updating latest prices")
        recent = _download([s for s in symbols if s in hist], "5d")
        for s, new in recent.items():
            comb = pd.concat([hist[s], new])
            hist[s] = comb[~comb.index.duplicated(keep="last")].sort_index()
    _state["hist"] = hist
    HIST_CACHE.write_bytes(pickle.dumps((hist, _state["hist_day"])))
    return hist


def earnings_dates(sym):
    key = f"earn:{sym}"
    c = store.cache_get(key, 86400)
    if c is not None:
        return [pd.Timestamp(x) for x in c]
    ts = []
    try:
        df = yf.Ticker(sym).get_earnings_dates(limit=12)
        if df is not None:
            for i in df.index:
                t = pd.Timestamp(i)
                t = t.tz_localize(ET) if t.tz is None else t.tz_convert(ET)
                ts.append(t)
    except Exception:
        pass
    store.cache_set(key, [t.isoformat() for t in ts])
    return ts


INFO_KEYS = ["longName", "shortName", "sector", "industry", "marketCap", "beta", "trailingPE", "forwardPE",
             "recommendationKey", "recommendationMean", "targetMeanPrice", "numberOfAnalystOpinions",
             "shortPercentOfFloat", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "quoteType", "dividendYield",
             "profitMargins", "revenueGrowth", "earningsGrowth", "floatShares", "sharesOutstanding",
             "totalRevenue", "heldPercentInstitutions", "exchange"]


def info(sym):
    key = f"info2:{sym}"
    c = store.cache_get(key, 6 * 3600)
    if c is not None:
        return c
    try:
        raw = yf.Ticker(sym).info or {}
        d = {k: raw.get(k) for k in INFO_KEYS}
    except Exception:
        d = {}
    store.cache_set(key, d)
    return d


def news(sym, name=None, n=8):
    key = f"news:{sym}"
    c = store.cache_get(key, 1800)
    if c is not None:
        return c
    out = []
    try:
        items = yf.Search(sym, news_count=15).news or []
        first = (name or "").split(" ")[0].lower().strip(",.")
        cutoff = time.time() - 4 * 86400
        for it in items:
            title = it.get("title", "")
            # Title must name the company or ticker; Yahoo's ticker tags include passing mentions.
            words = set(title.lower().replace("'s", "").replace(",", " ").replace(":", " ").split())
            relevant = (len(first) > 2 and first in words) or sym.lower() in words or f"({sym.lower()})" in words
            if relevant and it.get("providerPublishTime", 0) >= cutoff:
                out.append({"title": title, "publisher": it.get("publisher"), "link": it.get("link"),
                            "time": it.get("providerPublishTime")})
    except Exception:
        pass
    out = out[:n]
    store.cache_set(key, out)
    return out


def reverse_split(sym):
    """Date of a reverse split in the last 365 days, if any (ratio < 1 in Yahoo's split history)."""
    key = f"rsplit:{sym}"
    c = store.cache_get(key, 86400)
    if c is not None:
        return c.get("date")
    date = None
    try:
        sp = yf.Ticker(sym).splits
        if sp is not None and len(sp):
            sp.index = pd.to_datetime(sp.index).tz_localize(None)
            rev = sp[(sp < 1) & (sp.index >= pd.Timestamp.now() - pd.Timedelta(days=365))]
            if len(rev):
                d, ratio = rev.index[-1], rev.iloc[-1]
                date = f"1-for-{round(1 / ratio)} on {d:%b %d, %Y}"
    except Exception:
        pass
    store.cache_set(key, {"date": date})
    return date


def movers():
    c = store.cache_get("movers", 900)
    if c is not None:
        return c
    out = {}
    # small_cap_gainers is where pump-and-dumps show up: included (with looser limits) so they get
    # screened and flagged, and so the spike-outcome study keeps growing.
    for name in ("day_gainers", "day_losers", "most_actives", "small_cap_gainers"):
        min_cap, min_px = (20e6, 0.5) if name == "small_cap_gainers" else (MOVER_MIN_MARKET_CAP, MOVER_MIN_PRICE)
        try:
            for q in yf.screen(name, count=MOVERS_PER_LIST * 2).get("quotes", []):
                if (q.get("quoteType") == "EQUITY" and (q.get("marketCap") or 0) >= min_cap
                        and (q.get("regularMarketPrice") or 0) >= min_px and "." not in q["symbol"]
                        and q.get("exchange") not in ("PNK", "OQB", "OQX", "OEM", "OTC")):
                    out.setdefault(q["symbol"], name)
                if sum(1 for v in out.values() if v == name) >= MOVERS_PER_LIST:
                    break
        except Exception:
            pass
    store.cache_set("movers", out)
    return out


def implied_move(sym):
    """Options-implied move from the nearest at-the-money straddle (market's own priced-in expectation)."""
    key = f"iv:{sym}"
    c = store.cache_get(key, 1800)
    if c is not None:
        return c
    res = None
    try:
        t = yf.Ticker(sym)
        today = now_et().date().isoformat()
        exp = next((e for e in t.options if e > today), None)
        if exp:
            ch = t.option_chain(exp)
            price = t.fast_info.last_price

            def mid(df):
                r = df.iloc[(df["strike"] - price).abs().argsort()[:1]].iloc[0]
                bid, ask = r.get("bid", 0) or 0, r.get("ask", 0) or 0
                return ((bid + ask) / 2 if bid > 0 and ask > 0 else r["lastPrice"]), r["strike"]

            cm, strike = mid(ch.calls)
            pm, _ = mid(ch.puts)
            res = {"expiry": exp, "strike": float(strike), "straddle": float(cm + pm),
                   "pct": float((cm + pm) / price * 100)}
    except Exception:
        res = None
    store.cache_set(key, res)
    return res


# ---------------------------------------------------------------- feature frames
def _market_frame(hist):
    spy, vix = hist.get("SPY"), hist.get("^VIX")
    m = pd.DataFrame({"spy_close": spy.Close})
    m["vix"] = vix.Close.reindex(m.index).ffill() if vix is not None else np.nan
    return m


def build_frames(hist, symbols, drop_partial):
    mkt_hist = hist
    if drop_partial:
        mkt_hist = {k: (v.iloc[:-1] if len(v) and _bar_is_partial(v.index[-1]) else v) for k, v in hist.items()}
    mkt = _market_frame(mkt_hist)
    frames = {}
    for s in symbols:
        df = mkt_hist.get(s)
        if df is None or len(df) < 80:
            continue
        sec_etf = SECTOR_ETF.get((store.cache_get(f"info2:{s}", 10 ** 9) or {}).get("sector") or "")
        sec = mkt_hist.get(sec_etf).Close if sec_etf and sec_etf in mkt_hist else None
        try:
            frames[s] = F.build(df, mkt, earnings_dates(s), sec)
        except Exception:
            traceback.print_exc()
    return frames


# ---------------------------------------------------------------- learning
def grade_pending(hist):
    graded = 0
    for p in store.pending_predictions():
        df = hist.get(p["symbol"])
        if df is None:
            continue
        idx = df.index
        d = pd.Timestamp(p["date"])
        if d not in idx:
            continue
        i = idx.get_loc(d)
        if i + 1 >= len(idx) or _bar_is_partial(idx[i + 1]):
            continue
        r = (df.Close.iloc[i + 1] / df.Close.iloc[i] - 1) * 100
        store.grade(p["date"], p["symbol"], idx[i + 1].date().isoformat(), float(r),
                    int(abs(r) >= BIG_MOVE_PCT), int(r > 0))
        graded += 1
    return graded


def train(frames):
    rows = []
    for s, f in frames.items():
        t = f.iloc[60:].dropna(subset=["y_big"]).copy()
        t["symbol"] = s
        rows.append(t)
    data = pd.concat(rows)
    dates = data.index.unique().sort_values()
    days_ago = len(dates) - 1 - dates.get_indexer(data.index)

    misses = {(g["date"], g["symbol"]) for g in store.graded(400)
              if g["big"] is not None and abs(g["big"] - g["p_big"]) > MISS_THRESHOLD}
    keys = list(zip(data.index.strftime("%Y-%m-%d"), data["symbol"]))
    extra = np.array([MISS_WEIGHT if k in misses else 1.0 for k in keys])

    Xb, yb = data[F.BIG_FEATURES].to_numpy(float), data["y_big"].to_numpy(float)
    Xd, yd = data[F.DIR_FEATURES].to_numpy(float), data["y_up"].to_numpy(float)
    big, sb = select_and_fit(Xb, yb, days_ago, extra, HOLDOUT_DAYS)
    dirm, sd = select_and_fit(Xd, yd, days_ago, np.ones(len(yd)), HOLDOUT_DAYS)
    big.names, dirm.names = F.BIG_FEATURES, F.DIR_FEATURES

    through = max(f.index[-1] for f in frames.values()).date().isoformat()  # newest bar used
    run_at = now_et().isoformat(timespec="seconds")
    mid = f"{through}@{run_at[11:19]}"
    model = {"id": mid, "trained_through": through, "run_at": run_at, "n_train": int(len(yb)),
             "big": big.to_dict(), "dir": dirm.to_dict(), "big_stats": sb, "dir_stats": sd,
             "misses_upweighted": int((extra > 1).sum())}
    MODEL_PATH.write_text(json.dumps(clean(model)))
    store.save_model_run(clean({
        "id": mid, "run_at": run_at, "trained_through": through, "n_train": len(yb),
        "half_life": sb["half_life"], "dir_half_life": sd["half_life"],
        "misses_upweighted": int((extra > 1).sum()),
        "holdout_logloss": sb["holdout_logloss"], "baseline_logloss": sb["baseline_logloss"],
        "holdout_auc": sb["holdout_auc"], "dir_holdout_acc": sd["holdout_acc"],
        "dir_baseline_acc": sd["baseline_acc"], "base_rate": sb["base_rate"],
        "weights": json.dumps(dict(zip(F.BIG_FEATURES, big.coef.round(4).tolist()))),
        "dir_weights": json.dumps(dict(zip(F.DIR_FEATURES, dirm.coef.round(4).tolist()))),
    }))
    _step(f"Model retrained on {len(yb):,} rows through {through} "
          f"(holdout AUC {sb['holdout_auc']:.3f}, half-life {sb['half_life']})")
    return model


def load_model():
    if _state["model"] is None and MODEL_PATH.exists():
        _state["model"] = json.loads(MODEL_PATH.read_text())
    return _state["model"]


# ---------------------------------------------------------------- explanations
class Row(dict):
    """Attribute access to a feature row (a pandas Series would shadow names like .squeeze)."""
    __getattr__ = dict.get


def _f(x, default=np.nan):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(v) else v


def driver_text(name, r):
    rv = math.exp(_f(r.rel_volume, 0))
    texts = {
        "log_atr_pct": f"Normally moves about {r.atr_pct:.1f}% a day (14-day ATR)",
        "rv20": f"20-day volatility {r.rv20:.1f}% per day",
        "vol_expansion": "Volatility expanding vs its 3-month norm" if r.vol_expansion > 0
        else "Volatility contracting vs its 3-month norm",
        "rel_volume": f"Volume {rv:.1f}× its 20-day average",
        "abs_ret": f"Moved {r.ret1:+.1f}% this session",
        "abs_gap": f"Gapped {r.gap:+.1f}% at the open",
        "range_vs_atr": f"Intraday range {r.range_vs_atr:.1f}× normal",
        "squeeze": "Trading range squeezed tight (often precedes a breakout)" if r.squeeze < 0.25
        else f"Bollinger width at {r.squeeze * 100:.0f}th percentile",
        "rsi_extreme": f"RSI {r.rsi:.0f} — stretched" if abs(r.rsi - 50) > 20 else f"RSI {r.rsi:.0f}",
        "from_52w_high": f"{r.from_52w_high:.1f}% from 52-week high",
        "from_52w_low": f"{(math.exp(r.from_52w_low) - 1) * 100:.0f}% above 52-week low",
        "earnings_next": "Earnings report before next session",
        "earnings_today": "Reacting to an earnings report",
        "mkt_abs_ret": f"S&P 500 moved {r.mkt_ret:+.1f}%",
        "vix": f"VIX (market fear gauge) at {r.vix:.1f}",
        "streak": f"{int(r.streak)} big-move days in the last 2 weeks",
    }
    return texts.get(name, name)


def why_moved(r, info_d):
    ret, mkt = _f(r.ret1, 0), _f(r.mkt_ret, 0)
    sec = _f(getattr(r, "sector_ret", np.nan))
    ref, ref_name = (sec, SECTOR_ETF.get(info_d.get("sector") or "", "sector")) if not np.isnan(sec) else (mkt, "S&P 500")
    excess = ret - ref
    rv = math.exp(_f(r.rel_volume, 0))
    if r.earnings_today:
        kind = "Earnings reaction"
    elif abs(ret) < 1:
        kind = "Quiet session"
    elif np.sign(ret) == np.sign(ref) and abs(excess) <= 0.5 * abs(ret):
        kind = f"Moving with {'its sector' if ref_name != 'S&P 500' else 'the market'}"
    else:
        kind = "Stock-specific move"
    return {"kind": kind, "ret": ret, "mkt_ret": mkt, "sector_ret": None if np.isnan(sec) else sec,
            "sector_etf": ref_name if ref_name != "S&P 500" else None, "excess": excess, "rel_volume": rv,
            "gap": _f(r.gap)}


def checklist(r, i, next_earn):
    """Factual signals, each tagged bull / bear / caution. No opinion — every line cites the number."""
    out = []

    def add(label, value, sig):
        out.append({"label": label, "value": value, "signal": sig})

    c = r.close
    if not np.isnan(_f(r.sma50)):
        add("Price vs 50-day average", f"{(c / r.sma50 - 1) * 100:+.1f}%", "bull" if c > r.sma50 else "bear")
    if not np.isnan(_f(r.sma200)):
        add("Price vs 200-day average", f"{(c / r.sma200 - 1) * 100:+.1f}%", "bull" if c > r.sma200 else "bear")
        add("50-day vs 200-day trend", "Uptrend" if r.sma50 > r.sma200 else "Downtrend",
            "bull" if r.sma50 > r.sma200 else "bear")
    if not np.isnan(_f(r.ret63)) and not np.isnan(_f(r.spy_ret63)):
        d = r.ret63 - r.spy_ret63
        add("3-month return vs S&P 500", f"{r.ret63:+.1f}% vs {r.spy_ret63:+.1f}%", "bull" if d > 0 else "bear")
    if not np.isnan(_f(r.rsi)):
        add("RSI (14)", f"{r.rsi:.0f}", "caution" if r.rsi > 70 or r.rsi < 30 else "neutral")
    rm = _f(i.get("recommendationMean"))
    if not np.isnan(rm):
        add("Analyst consensus", f"{(i.get('recommendationKey') or '').replace('_', ' ')} "
            f"({rm:.1f}/5, {i.get('numberOfAnalystOpinions') or 0} analysts)",
            "bull" if rm <= 2.2 else "bear" if rm >= 3 else "neutral")
    tgt = _f(i.get("targetMeanPrice"))
    if not np.isnan(tgt) and c:
        up = (tgt / c - 1) * 100
        add("Avg analyst price target", f"${tgt:,.2f} ({up:+.1f}%)", "bull" if up > 10 else "bear" if up < 0 else "neutral")
    tpe, fpe = _f(i.get("trailingPE")), _f(i.get("forwardPE"))
    if not np.isnan(fpe):
        if not np.isnan(tpe):
            add("Forward vs trailing P/E", f"{fpe:.1f} vs {tpe:.1f}",
                "bull" if fpe < tpe else "bear")
        elif fpe < 0:
            add("Forward P/E", f"{fpe:.1f} (losses expected)", "bear")
    eg = _f(i.get("earningsGrowth"))
    if not np.isnan(eg):
        add("Earnings growth (yoy)", f"{eg * 100:+.1f}%", "bull" if eg > 0 else "bear")
    rg = _f(i.get("revenueGrowth"))
    if not np.isnan(rg):
        add("Revenue growth (yoy)", f"{rg * 100:+.1f}%", "bull" if rg > 0 else "bear")
    si = _f(i.get("shortPercentOfFloat"))
    if not np.isnan(si):
        add("Short interest", f"{si * 100:.1f}% of float", "caution" if si > 0.15 else "neutral")
    rv = math.exp(_f(r.rel_volume, 0))
    if rv >= 1.5 and abs(_f(r.ret1, 0)) >= 1:
        add("Volume confirms today's move", f"{r.ret1:+.1f}% on {rv:.1f}× volume", "bull" if r.ret1 > 0 else "bear")
    if next_earn is not None:
        days = (next_earn.date() - now_et().date()).days
        if 0 <= days <= 10:
            add("Upcoming earnings", f"{next_earn:%b %d} ({days}d) — event risk", "caution")
    return out


# ---------------------------------------------------------------- main refresh
def refresh():
    if not _refresh_lock.acquire(blocking=False):
        return False
    STATUS.update(running=True, last_error=None)
    try:
        wl = store.watchlist()
        mv = movers()
        pending_syms = {p["symbol"] for p in store.pending_predictions()}
        symbols = list(dict.fromkeys(UNIVERSE + wl + list(mv) + sorted(pending_syms)))
        _step(f"Fetching company data for {len(symbols)} symbols")
        with ThreadPoolExecutor(10) as ex:
            infos = dict(zip(symbols, ex.map(info, symbols)))
            _step("Fetching earnings calendars")
            list(ex.map(earnings_dates, symbols))
        hist = load_history(symbols + MARKET_TICKERS + list(SECTOR_ETF.values()))

        _step("Grading past predictions against what actually happened")
        n_graded = grade_pending(hist)

        model = load_model()
        spy_idx = hist["SPY"].index
        last_complete = spy_idx[-2] if _bar_is_partial(spy_idx[-1]) else spy_idx[-1]
        if model is None or model["trained_through"] < last_complete.date().isoformat():
            _step("Retraining model with the newest outcomes")
            train_syms = [s for s in symbols if s in hist and s not in mv]
            model = train(build_frames(hist, train_syms, drop_partial=True))
            _state["model"] = model
        big, dirm = LogReg.from_dict(model["big"]), LogReg.from_dict(model["dir"])
        dir_edge = model["dir_stats"]["holdout_acc"] > model["dir_stats"]["baseline_acc"] + 0.01

        _step("Scoring stocks")
        live = build_frames(hist, symbols, drop_partial=False)
        t = now_et()
        elapsed = min(max(((t.hour * 60 + t.minute) - 570) / 390, 0.05), 1.0)
        cards, preds, card_frames = [], [], {}
        for s, f in live.items():
            r = f.iloc[-1].copy()
            partial = _bar_is_partial(f.index[-1])
            if partial:  # project today's volume to a full session before comparing to the average
                r["rel_volume"] = math.log(max(r.volume / elapsed / r.avg_vol20, 0.05)) if r.avg_vol20 else np.nan
                r["rel_volume_signed"] = r.rel_volume * np.sign(r.ret1)
            xb, xd = r[F.BIG_FEATURES].to_numpy(float), r[F.DIR_FEATURES].to_numpy(float)
            r = Row(r.to_dict())
            p_big, p_up = float(big.predict(xb)[0]), float(dirm.predict(xd)[0])
            contrib = big.contributions(xb)
            order = np.argsort(-contrib)
            drivers = [{"text": driver_text(F.BIG_FEATURES[j], r), "impact": float(contrib[j])}
                       for j in order[:3] if contrib[j] > 0.05]
            i = infos.get(s) or {}
            ed = [e for e in earnings_dates(s) if e.date() >= t.date()]
            next_earn = min(ed) if ed else None
            cl = checklist(r, i, next_earn)
            jump = f.ret1.iloc[-15:].abs().max()
            warn = (f"A {jump:.0f}% one-day price change in the last 3 weeks — may be a split/spin-off the data source "
                    "did not adjust; volatility readings could be inflated.") if jump > 40 else None
            cards.append({
                "symbol": s, "name": i.get("longName") or i.get("shortName") or s, "sector": i.get("sector"),
                "market_cap": i.get("marketCap"), "price": r.close, "prev_close": r.prev_close,
                "change": r.close - r.prev_close, "change_pct": r.ret1, "gap_pct": r.gap,
                "day_high": r.high, "day_low": r.low, "volume": r.volume, "rel_volume": math.exp(_f(r.rel_volume, 0)),
                "atr_pct": r.atr_pct, "bar_date": f.index[-1].date().isoformat(), "partial": partial,
                "p_big": p_big, "lift": p_big / model["big_stats"]["base_rate"], "p_up": p_up, "dir_edge": dir_edge,
                "drivers": drivers, "why": why_moved(r, i), "next_earnings": next_earn,
                "checklist": cl, "bull": sum(c["signal"] == "bull" for c in cl),
                "bear": sum(c["signal"] == "bear" for c in cl), "caution": sum(c["signal"] == "caution" for c in cl),
                "mover": mv.get(s), "watch": s in wl, "news": [], "data_warning": warn,
            })
            card_frames[s] = f
            preds.append({"date": f.index[-1].date().isoformat(), "symbol": s, "p_big": p_big, "p_up": p_up,
                          "price": float(r.close), "model_id": model["id"], "made_at": t.isoformat(timespec="seconds"),
                          "features": json.dumps(clean(dict(zip(F.BIG_FEATURES, xb))))})
        store.save_predictions(preds)

        cards.sort(key=lambda c: -c["p_big"])
        need_news = [c for k, c in enumerate(cards) if k < NEWS_TOP_N or c["watch"] or abs(_f(c["change_pct"], 0)) >= 3]
        _step(f"Fetching headlines for {len(need_news)} stocks")
        with ThreadPoolExecutor(8) as ex:
            for c, n in zip(need_news, ex.map(lambda c: news(c["symbol"], c["name"]), need_news)):
                c["news"] = n
        _step("Screening for pump-and-dump warning signs")
        small = [c for c in cards if (c["market_cap"] or 0) < 2e9 or (c["price"] or 0) < 10]
        with ThreadPoolExecutor(8) as ex:
            rsplits = dict(zip([c["symbol"] for c in small], ex.map(lambda c: reverse_split(c["symbol"]), small)))
        for c in cards:
            c["pump"] = pump.assess(card_frames[c["symbol"]], infos.get(c["symbol"]) or {}, c["news"],
                                    rsplits.get(c["symbol"]))
            if c["pump"]["level"] != "low":
                c["checklist"].append({"label": "Pump-and-dump warning signs",
                                       "value": f"{c['pump']['level']} risk ({c['pump']['score']}/100)",
                                       "signal": "bear" if c["pump"]["level"] == "high" else "caution"})
                c["bear" if c["pump"]["level"] == "high" else "caution"] += 1
        spike_stats = pump.spike_aftermath(hist)
        snap = clean({"cards": cards, "spike_stats": spike_stats, "generated_at": t.isoformat(timespec="seconds"), "session": session_state(t),
                      "big_move_pct": BIG_MOVE_PCT, "model": {k: model[k] for k in
                      ("id", "trained_through", "run_at", "n_train", "big_stats", "dir_stats", "misses_upweighted")},
                      "graded_this_run": n_graded})
        _state["snapshot"] = snap
        store.cache_set("snapshot", snap)
        STATUS["last_refresh"] = snap["generated_at"]
        _step("Publishing to the private online site")
        _step(f"Done — {len(cards)} stocks scored · online: {publish_online()}")
        return True
    except Exception as e:
        traceback.print_exc()
        STATUS["last_error"] = f"{type(e).__name__}: {e}"
        _step("Failed")
        return False
    finally:
        STATUS["running"] = False
        _refresh_lock.release()


def snapshot():
    if _state["snapshot"] is None:
        _state["snapshot"] = store.cache_get("snapshot", 10 ** 9)
    return _state["snapshot"]


# ---------------------------------------------------------------- detail & performance
def detail(sym):
    sym = sym.upper()
    snap = snapshot() or {}
    card = next((c for c in snap.get("cards", []) if c["symbol"] == sym), None)
    hist = _state["hist"] or {}
    df = hist.get(sym)
    if df is None:
        df = _download([sym], "1y").get(sym)
    chart = []
    if df is not None:
        d = df.tail(260).copy()
        d["sma50"] = df.Close.rolling(50).mean().tail(260)
        chart = [{"d": i.date().isoformat(), "c": r.Close, "s": r.sma50, "v": r.Volume} for i, r in d.iterrows()]
    model = load_model()
    all_drivers = []
    feats = store.latest_features(sym)
    if feats and model:
        big = LogReg.from_dict(model["big"])
        x = np.array([_f(feats.get(n)) for n in F.BIG_FEATURES])
        all_drivers = sorted([{"feature": n, "value": feats.get(n), "impact": float(v)}
                              for n, v in zip(F.BIG_FEATURES, big.contributions(x))], key=lambda z: -abs(z["impact"]))
    return clean({"card": card, "chart": chart, "history": store.symbol_history(sym, 40),
                  "news": news(sym, (card or {}).get("name")), "implied_move": implied_move(sym),
                  "info": info(sym), "all_drivers": all_drivers})


def top_pick(c):
    """Same checks as the Top 5 view in static/app.js."""
    return ((c.get("pump") or {}).get("level") == "low" and not c.get("data_warning") and c["bull"] >= c["bear"]
            and (c.get("market_cap") or 0) >= 2e9 and (c.get("price") or 0) >= 10)


def publish_online():
    """Bundle snapshot + track record + detail views for the stocks most likely to be opened, and upload."""
    try:
        snap = snapshot()
        cards = snap["cards"]
        syms = [c["symbol"] for c in cards if top_pick(c)][:15]
        syms += [c["symbol"] for c in cards[:30]] + [c["symbol"] for c in cards if c["watch"]]
        syms = list(dict.fromkeys(syms))
        with ThreadPoolExecutor(6) as ex:
            details = dict(zip(syms, ex.map(detail, syms)))
        return publish.upload({"snapshot": snap, "performance": performance(), "details": details,
                               "status": dict(STATUS, running=False)})
    except Exception as e:
        traceback.print_exc()
        return f"failed: {e}"


def performance():
    g = store.graded()
    model = load_model()
    out = {"n": len(g), "big_move_pct": BIG_MOVE_PCT, "runs": store.model_runs(30),
           "model": {k: model[k] for k in ("id", "trained_through", "n_train", "big_stats", "dir_stats",
                                          "misses_upweighted")} if model else None}
    if model:
        out["weights"] = sorted([{"feature": n, "w": w} for n, w in zip(model["big"]["names"], model["big"]["coef"])],
                                key=lambda z: -abs(z["w"]))
        out["dir_weights"] = sorted([{"feature": n, "w": w} for n, w in zip(model["dir"]["names"], model["dir"]["coef"])],
                                    key=lambda z: -abs(z["w"]))
    if not g:
        return clean(out)
    df = pd.DataFrame(g)
    y, p = df.big.to_numpy(float), df.p_big.to_numpy(float)
    base = y.mean()
    df["rank"] = df.groupby("date").p_big.rank(ascending=False)
    top = df[df["rank"] <= 10]
    bigs = df[df.big == 1]
    out.update({
        "actual_rate": base, "avg_pred": p.mean(),
        "brier": float(np.mean((p - y) ** 2)), "brier_baseline": float(np.mean((base - y) ** 2)),
        "auc": auc(y, p), "top10_hit": float(top.big.mean()) if len(top) else None,
        "dir_acc_on_big": float(((bigs.p_up > 0.5) == (bigs.up == 1)).mean()) if len(bigs) else None,
        "n_big": int(len(bigs)),
    })
    bins = [0, .1, .2, .35, .5, 1.01]
    df["bucket"] = pd.cut(df.p_big, bins, right=False)
    out["calibration"] = [{"range": f"{b.left:.0%}–{min(b.right, 1):.0%}", "predicted": grp.p_big.mean(),
                           "actual": grp.big.mean(), "n": len(grp)}
                          for b, grp in df.groupby("bucket", observed=True) if len(grp)]
    daily = []
    for d, grp in df.groupby("date"):
        t10 = grp.nsmallest(10, "rank")
        daily.append({"date": d, "n": len(grp), "actual_rate": grp.big.mean(), "top10_hit": t10.big.mean(),
                      "brier": float(np.mean((grp.p_big - grp.big) ** 2))})
    out["daily"] = daily[-60:]
    df["err"] = (df.big - df.p_big).abs()
    out["misses"] = df[df.err > MISS_THRESHOLD].sort_values("date", ascending=False).head(25)[
        ["date", "symbol", "p_big", "actual_ret", "big"]].to_dict("records")
    return clean(out)
