"""Does the opening-range breakout (ORB) plan make money on the stocks Move Radar picks?
Run:  python -m research.orb_study

Rules fixed before looking at results:
  * Data: last ~60 sessions of 5-minute bars (Yahoo's limit), core universe.
  * Picks: each day, the top 5 by big-move chance at the PRIOR close (price >= $10), scored by a model
    trained only on data before the test window (no hindsight in the selection).
  * Trade: range = first N minutes. First break of the high (long) or low (short), entry at the range edge
    (or the bar open if it gaps through), stop = other side of the range, exit at stop or the 15:55 bar close.
    If the entry bar also touches the stop, it counts as stopped. One trade per stock per day.
  * Costs: 0.10% of price per round trip. Results in R (1R = range width = amount risked).
  * Pass = average R > 0 after costs, day-clustered t >= 2, and positive in both halves of the window.
  * Primary test: picks, 30-minute range. 15/60-minute ranges and "all stocks" are secondary.
Results go to the console and data/research/orb_study.json."""
import json
import math
import pickle

import numpy as np
import pandas as pd
import yfinance as yf

from radar import engine, store
from radar import features as F
from radar.config import DATA_DIR, HIST_CACHE, UNIVERSE
from radar.model import LogReg

COST = 0.001
RANGES = (30, 15, 60)
TOP_N = 5


def tstat(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")


def intraday(symbols):
    out = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        d = yf.download(chunk, period="60d", interval="5m", group_by="ticker", auto_adjust=False,
                        progress=False, threads=True)
        for s in chunk:
            try:
                b = d[s].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(b):
                b.index = b.index.tz_convert("America/New_York")
                out[s] = b
    return out


def trade(day_bars, minutes):
    """One ORB trade on one day's 5-minute bars. Returns dict or None (no break / no data)."""
    mins = day_bars.index.hour * 60 + day_bars.index.minute
    b = day_bars[(mins >= 570) & (mins < 960)]
    mins = b.index.hour * 60 + b.index.minute
    rng = b[mins < 570 + minutes]
    rest = b[mins >= 570 + minutes]
    if len(rng) < minutes // 5 or not len(rest):
        return None
    hi, lo = rng.High.max(), rng.Low.min()
    width = hi - lo
    if width <= 0:
        return None
    for i, (t, bar) in enumerate(rest.iterrows()):
        up, dn = bar.High > hi, bar.Low < lo
        if not (up or dn):
            continue
        if up and dn:  # broke both sides inside one bar: can't know the order, assume the worse outcome
            return {"side": "both", "r": -1.0 - COST * bar.Open / width, "time": t}
        side = 1 if up else -1
        entry = max(bar.Open, hi) if up else min(bar.Open, lo)
        stop = lo if up else hi
        risk = abs(entry - stop)
        if risk <= 0:
            return None
        cost_r = COST * entry / risk
        # stopped in the entry bar?
        if (up and bar.Low <= stop) or (dn and bar.High >= stop):
            return {"side": "long" if up else "short", "r": -1.0 - cost_r, "time": t}
        for _, nb in rest.iloc[i + 1:].iterrows():
            if (up and nb.Low <= stop) or (dn and nb.High >= stop):
                return {"side": "long" if up else "short", "r": -1.0 - cost_r, "time": t}
        exit_px = rest.Close.iloc[-1]
        return {"side": "long" if up else "short", "r": side * (exit_px - entry) / risk - cost_r, "time": t}
    return None  # never broke out


def walk_forward_picks(hist, syms, test_days):
    """Top-N by big-move chance at each prior close, from a model trained before the test window."""
    frames = engine.build_frames(hist, syms, drop_partial=True)
    start = test_days[0]
    rows = []
    for s, f in frames.items():
        f = f.iloc[60:].copy()
        f["symbol"] = s
        rows.append(f)
    data = pd.concat(rows)
    train = data[(data.index < start - pd.Timedelta(days=3)) & data.y_big.notna()]
    model = LogReg(F.BIG_FEATURES).fit(train[F.BIG_FEATURES].to_numpy(float), train.y_big.to_numpy(float))
    data["p"] = model.predict(data[F.BIG_FEATURES].to_numpy(float))
    sessions = data.index.unique().sort_values()
    picks = {}
    for d in test_days:
        pos = sessions.searchsorted(d)
        if pos == 0:
            continue
        prev = sessions[pos - 1]
        g = data.loc[[prev]] if prev in data.index else None
        if g is None:
            continue
        g = g[g.close >= 10].sort_values("p", ascending=False)
        picks[d] = list(g.symbol.iloc[:TOP_N])
    return picks


def summarize(trades, label):
    if not trades:
        return {"group": label, "trades": 0}
    t = pd.DataFrame(trades)
    per_day = t.groupby("day").r.mean()
    days = per_day.index.sort_values()
    mid = days[len(days) // 2]
    a, b = per_day[per_day.index < mid], per_day[per_day.index >= mid]
    wins = t.r[t.r > 0].sum()
    losses = -t.r[t.r < 0].sum()
    res = {"group": label, "trades": len(t), "days": len(per_day), "avg_r": float(t.r.mean()),
           "win_rate": float((t.r > 0).mean()), "profit_factor": float(wins / losses) if losses else None,
           "t_day_clustered": tstat(per_day), "first_half_avg_r": float(a.mean()), "second_half_avg_r": float(b.mean()),
           "long_share": float((t.side == "long").mean()), "no_break_share": None}
    res["passes"] = bool(res["avg_r"] > 0 and res["t_day_clustered"] >= 2 and a.mean() > 0 and b.mean() > 0)
    return res


def main():
    store.init()
    hist, _ = pickle.loads(HIST_CACHE.read_bytes())
    syms = [s for s in UNIVERSE if s in hist]
    print(f"Downloading 5-minute bars for {len(syms)} symbols...")
    bars = intraday(syms)
    days = sorted({pd.Timestamp(d) for b in bars.values() for d in b.index.date})
    days = [d for d in days if d < pd.Timestamp(engine.now_et().date()) or engine.session_state() in ("after", "closed")]
    print(f"Sessions: {days[0].date()} to {days[-1].date()} ({len(days)})")
    picks = walk_forward_picks(hist, syms, days)
    results = []
    for minutes in RANGES:
        sel, allt, nobreak = [], [], 0
        for s, b in bars.items():
            for d, day_bars in b.groupby(b.index.date):
                d = pd.Timestamp(d)
                if d not in picks and d not in days:
                    continue
                tr = trade(day_bars, minutes)
                if tr is None:
                    if s in picks.get(d, []):
                        nobreak += 1
                    continue
                tr.update(day=d, symbol=s)
                allt.append(tr)
                if s in picks.get(d, []):
                    sel.append(tr)
        for label, tl in ((f"Picks (top {TOP_N}), {minutes}-min range", sel), (f"All core stocks, {minutes}-min range", allt)):
            r = summarize(tl, label)
            if label.startswith("Picks"):
                r["no_break_share"] = nobreak / (nobreak + len(sel)) if (nobreak + len(sel)) else None
            r["primary"] = minutes == 30 and label.startswith("Picks")
            results.append(r)
    for r in results:
        if not r.get("trades"):
            print(f"{r['group']}: no trades")
            continue
        flag = "PASS" if r["passes"] else "FAIL"
        print(f"{flag}{' (primary)' if r['primary'] else ''}  {r['group']:<34} trades {r['trades']:<5} days {r['days']:<3} "
              f"avg {r['avg_r']:+.3f}R  win {r['win_rate']:.0%}  PF {r['profit_factor'] or 0:.2f}  t {r['t_day_clustered']:.1f}  "
              f"halves {r['first_half_avg_r']:+.3f}R / {r['second_half_avg_r']:+.3f}R  long {r['long_share']:.0%}"
              + (f"  no-break {r['no_break_share']:.0%}" if r.get("no_break_share") is not None else ""))
    out = DATA_DIR / "research"
    out.mkdir(exist_ok=True)
    (out / "orb_study.json").write_text(json.dumps(engine.clean({
        "run_at": engine.now_et().isoformat(timespec="seconds"), "sessions": [str(days[0].date()), str(days[-1].date()), len(days)],
        "results": results}), indent=1))
    print(f"Saved {out / 'orb_study.json'}")


if __name__ == "__main__":
    main()
