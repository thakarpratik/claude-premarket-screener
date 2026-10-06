"""Can anything in our data call DIRECTION better than chance?  Run:  python -m research.direction_study

Rules fixed before looking at results:
  * Models are walk-forward: each test month is predicted by a model trained only on earlier data
    (with a gap of `h` days so overlapping targets can't leak).
  * Event effects are measured as excess return vs SPY and must have |t| >= 2 with the same sign in
    BOTH the first and second half of the sample to count as real.
  * Universe = the fixed core list (not today's movers, which were picked *because* they moved).
Results go to the console and data/research/direction_study.json."""
import json
import math
import pickle
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import yfinance as yf

from radar import engine, store
from radar import features as F
from radar.config import DATA_DIR, HIST_CACHE, UNIVERSE
from radar.model import LogReg, auc

ET = engine.ET
HORIZONS = (1, 5, 20)
TEST_MONTHS = 9


def tstat(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 and x.std() > 0 else float("nan")


def earnings_table(sym):
    """Past earnings with surprise %, keyed to the reaction session."""
    try:
        d = yf.Ticker(sym).get_earnings_dates(limit=12)
    except Exception:
        return []
    out = []
    if d is None:
        return out
    for ts, row in d.iterrows():
        if pd.isna(row.get("Surprise(%)")):
            continue
        t = pd.Timestamp(ts)
        t = t.tz_localize(ET) if t.tz is None else t.tz_convert(ET)
        out.append((t, float(row["Surprise(%)"])))
    return out


def reaction_day(index, ts):
    d = pd.Timestamp(ts.date())
    pos = index.searchsorted(d)
    if pos < len(index) and index[pos] == d and ts.hour >= 12:
        pos += 1
    return pos if pos < len(index) else None


def build_dataset():
    hist, _ = pickle.loads(HIST_CACHE.read_bytes())
    syms = [s for s in UNIVERSE if s in hist]
    print(f"Universe: {len(syms)} symbols, history {hist['SPY'].index[0].date()} to {hist['SPY'].index[-1].date()}")
    frames = engine.build_frames(hist, syms, drop_partial=True)
    with ThreadPoolExecutor(10) as ex:
        earn = dict(zip(syms, ex.map(earnings_table, syms)))
    spy = hist["SPY"].Close
    rows = []
    for s, f in frames.items():
        c = f["close"]
        f = f.copy()
        for h in HORIZONS:
            f[f"fwd{h}"] = (c.shift(-h) / c - 1) * 100
            f[f"spy{h}"] = (spy.reindex(f.index).shift(-h) / spy.reindex(f.index) - 1) * 100
        f["ret60"] = c.pct_change(60) * 100
        # earnings surprise features: most recent surprise within the last 60 sessions
        f["sue"], f["since_earn"], f["earn_react"], f["is_react"] = 0.0, 999.0, 0.0, 0.0
        f["surprise_today"] = np.nan
        for ts, sp in earn.get(s, []):
            p = reaction_day(f.index, ts)
            if p is None:
                continue
            react_ret = f["ret1"].iloc[p]
            f.iloc[p, f.columns.get_loc("is_react")] = 1.0
            f.iloc[p, f.columns.get_loc("surprise_today")] = sp
            end = min(p + 60, len(f))
            for k in range(p, end):
                if f["since_earn"].iloc[k] > k - p:
                    f.iloc[k, f.columns.get_loc("sue")] = np.clip(sp, -50, 50)
                    f.iloc[k, f.columns.get_loc("since_earn")] = k - p
                    f.iloc[k, f.columns.get_loc("earn_react")] = react_ret
        f["symbol"] = s
        rows.append(f.iloc[60:])
    data = pd.concat(rows)
    data.index.name = "date"
    print(f"Rows: {len(data):,}; earnings events with surprise: {int(data.is_react.sum())}")
    return data


EXTRA = ["ret60", "from_52w_high", "rv20", "sue", "earn_react"]


def walk_forward(data, h, relative):
    feats = F.DIR_FEATURES + EXTRA
    d = data.dropna(subset=[f"fwd{h}"]).copy()
    if relative:  # beat the median stock over the same window? (market-neutral)
        d["y"] = (d[f"fwd{h}"] > d.groupby(level=0)[f"fwd{h}"].transform("median")).astype(float)
    else:
        d["y"] = (d[f"fwd{h}"] > 0).astype(float)
    dates = d.index.unique().sort_values()
    months = pd.PeriodIndex(dates, freq="M").unique()[-TEST_MONTHS:]
    preds = []
    for m in months:
        test_mask = pd.PeriodIndex(d.index, freq="M") == m
        start = d.index[test_mask].min()
        cut = dates[max(dates.searchsorted(start) - h, 0)]  # embargo h sessions
        tr = d[d.index < cut]
        te = d[test_mask]
        if len(tr) < 5000 or not len(te):
            continue
        model = LogReg(feats).fit(tr[feats].to_numpy(float), tr["y"].to_numpy(float))
        p = model.predict(te[feats].to_numpy(float))
        preds.append(pd.DataFrame({"p": p, "y": te["y"].to_numpy(), "fwd": te[f"fwd{h}"].to_numpy(),
                                   "train_up": tr["y"].mean()}, index=te.index))
    P = pd.concat(preds)
    acc = float(((P.p > 0.5) == (P.y == 1)).mean())
    always_up = float(P.y.mean())
    train_majority = float(np.mean(np.where(P.train_up >= 0.5, P.y == 1, P.y == 0)))
    # long-short: each date, top 20% by p minus bottom 20% (sample every h sessions to avoid overlap)
    ls = []
    for i, (dt, g) in enumerate(P.groupby(level=0)):
        if i % h or len(g) < 20:
            continue
        q = g.p.rank(pct=True)
        ls.append(g.fwd[q >= 0.8].mean() - g.fwd[q <= 0.2].mean())
    return {"horizon": h, "target": "beat median stock" if relative else "up vs down", "n": int(len(P)),
            "accuracy": acc, "always_up": always_up, "train_majority": train_majority, "auc": auc(P.y.to_numpy(), P.p.to_numpy()),
            "edge_vs_best_baseline_pts": (acc - max(always_up, train_majority, 0.5)) * 100,
            "long_short_mean_pct": float(np.nanmean(ls)), "long_short_t": tstat(ls), "long_short_periods": len(ls)}


def event(data, mask, h, label):
    x = (data.loc[mask, f"fwd{h}"] - data.loc[mask, f"spy{h}"]).dropna()
    mid = data.index.unique().sort_values()
    mid = mid[len(mid) // 2]
    a, b = x[x.index < mid], x[x.index >= mid]
    res = {"event": label, "horizon": h, "n": int(len(x)), "mean_excess_pct": float(x.mean()) if len(x) else None,
           "hit_up_pct": float((x > 0).mean() * 100) if len(x) else None, "t": tstat(x),
           "first_half": {"n": len(a), "mean": float(a.mean()) if len(a) else None, "t": tstat(a)},
           "second_half": {"n": len(b), "mean": float(b.mean()) if len(b) else None, "t": tstat(b)}}
    ta, tb = res["first_half"]["t"], res["second_half"]["t"]
    res["passes"] = bool(abs(ta) >= 2 and abs(tb) >= 2 and np.sign(ta) == np.sign(tb))
    return res


def events(data):
    out = []
    big = data["ret1"].abs() >= 2 * data["atr_pct"]
    gap_up, gap_dn = (data["gap"] >= 4) & (data.is_react == 0), (data["gap"] <= -4) & (data.is_react == 0)
    tests = {
        "Earnings beat (surprise > +5%)": (data.is_react == 1) & (data.surprise_today > 5),
        "Earnings miss (surprise < 0%)": (data.is_react == 1) & (data.surprise_today < 0),
        "Beat AND stock rose on the day": (data.is_react == 1) & (data.surprise_today > 0) & (data.ret1 > 0),
        "Miss AND stock fell on the day": (data.is_react == 1) & (data.surprise_today < 0) & (data.ret1 < 0),
        "Earnings day: rose 5%+": (data.is_react == 1) & (data.ret1 >= 5),
        "Earnings day: fell 5%+": (data.is_react == 1) & (data.ret1 <= -5),
        "Gap up 4%+ (no earnings)": gap_up,
        "Gap down 4%+ (no earnings)": gap_dn,
        "Big up day (2x normal range)": big & (data.ret1 > 0) & (data.is_react == 0),
        "Big down day (2x normal range)": big & (data.ret1 < 0) & (data.is_react == 0),
    }
    for label, m in tests.items():
        for h in HORIZONS:
            out.append(event(data, m, h, label))
    return out


def main():
    store.init()
    data = build_dataset()
    print("\n=== A. Models (walk-forward, last %d months) ===" % TEST_MONTHS)
    models = []
    for rel in (False, True):
        for h in HORIZONS:
            r = walk_forward(data, h, rel)
            models.append(r)
            print(f"{r['target']:>17} {h:>2}d  acc {r['accuracy']:.3f}  always-up {r['always_up']:.3f}  "
                  f"train-majority {r['train_majority']:.3f}  edge {r['edge_vs_best_baseline_pts']:+.1f} pts  AUC {r['auc']:.3f}  "
                  f"top-minus-bottom {r['long_short_mean_pct']:+.2f}% (t {r['long_short_t']:.1f}, {r['long_short_periods']} periods)")
    print("\n=== B. Situations (excess return vs SPY; must hold in both halves) ===")
    ev = events(data)
    for r in ev:
        flag = "PASS" if r["passes"] else "    "
        print(f"{flag} {r['event']:<32} {r['horizon']:>2}d  n={r['n']:<4} mean {r['mean_excess_pct']:+.2f}%  up {r['hit_up_pct']:.0f}%  "
              f"t {r['t']:.1f}  | 1st half {r['first_half']['mean']:+.2f}% (t {r['first_half']['t']:.1f})  "
              f"2nd half {r['second_half']['mean']:+.2f}% (t {r['second_half']['t']:.1f})")
    out = DATA_DIR / "research"
    out.mkdir(exist_ok=True)
    (out / "direction_study.json").write_text(json.dumps(engine.clean({
        "run_at": engine.now_et().isoformat(timespec="seconds"), "rows": len(data),
        "models": models, "events": ev,
        "untestable": ["Options flow / put-call skew: no free historical data (only today's chains).",
                       "Analyst estimate revisions: no free historical data."]}), indent=1))
    print(f"\nSaved {out / 'direction_study.json'}")


if __name__ == "__main__":
    main()
