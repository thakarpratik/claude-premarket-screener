"""Learning loop for the long-setup score.

Each run journals every scored setup (features, trigger, invalidation). On a later
run, pending entries are graded against that day's 5-minute bars:

  * entry  = first bar (after the pick was made, 09:30 or later) that trades at/above
             the trigger; fill at max(trigger, bar open)
  * exit   = invalidation level if hit after entry, otherwise the 16:00 close
  * result = R-multiple = (exit - entry) / (entry - invalidation)

The weights then take one bounded gradient step: prediction p = score/100, target
y = 1 if R > 0 else 0, and each feature's weight moves by LR * (y - p) * feature.
Predictions that were "completely wrong" (|y - p| >= WRONG_GAP) get a bigger step.
Changes are capped per day and weights are bounded, so a single noisy day
cannot rewrite the model. Setups that never triggered are recorded but not
learned from (no trade happened).
"""
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
JOURNAL_DIR = HERE / "journal"
PICKS = JOURNAL_DIR / "picks.jsonl"
WEIGHTS = JOURNAL_DIR / "weights.json"
WEIGHTS_HISTORY = JOURNAL_DIR / "weights_history.jsonl"

# Starting weights = the hand-tuned model. Features are 0/1 except move_size (0..1).
DEFAULT_WEIGHTS = {
    "fresh_news": 25, "older_news": 12, "earnings": 10, "upgrade": 10, "downgrade": -10,
    "move_size": 20, "above_50dma": 8, "above_200dma": 7, "rsi_ok": 5, "rsi_hot": -10,
    "extended_20d": -5, "holding_gap": 10, "fading_gap": -10, "sector_green": 5, "sector_red": 0,
    "market_green": 5, "liquid": 5, "large_cap": 5, "mcap_unknown": -15, "low_float": -10,
    "pump_pattern": -15, "parabolic": -10,
}
FEATURE_LABELS = {
    "fresh_news": "Fresh company headline (<18h)", "older_news": "Older company headline",
    "earnings": "Earnings event", "upgrade": "Analyst upgrade", "downgrade": "Analyst downgrade",
    "move_size": "Size of move vs ATR", "above_50dma": "Above 50-day avg", "above_200dma": "Above 200-day avg",
    "rsi_ok": "RSI < 70 before gap", "rsi_hot": "RSI ≥ 75 before gap", "extended_20d": "Up >30% in 20d",
    "holding_gap": "Holding near PM high", "fading_gap": "Fading from PM high", "sector_green": "Sector green",
    "sector_red": "Sector red", "market_green": "S&P futures green", "liquid": "$ volume ≥ $100M",
    "large_cap": "Market cap ≥ $10B", "mcap_unknown": "Market cap unknown", "low_float": "Low float",
    "pump_pattern": "Huge move, no news", "parabolic": "Up >100% in 20d",
}
LR = 6.0                 # points per unit of error
WRONG_GAP = 0.6          # |y - p| at/above this = "completely wrong"
WRONG_BOOST = 2.0        # step multiplier for completely wrong calls
MAX_DAILY_CHANGE = 3.0   # max points any weight can move in one day
WEIGHT_BOUNDS = (-30.0, 35.0)
MIN_TRADES_TO_LEARN = 3  # need at least this many triggered trades in a day to update
MAX_EVAL_AGE_DAYS = 55   # Yahoo keeps ~60 days of 5-minute bars


# ---------------------------------------------------------------- storage

def load_weights() -> dict:
    try:
        w = json.loads(WEIGHTS.read_text())["weights"]
        return {k: float(w.get(k, v)) for k, v in DEFAULT_WEIGHTS.items()}
    except Exception:
        return {k: float(v) for k, v in DEFAULT_WEIGHTS.items()}


def save_weights(w: dict, note: dict):
    JOURNAL_DIR.mkdir(exist_ok=True)
    WEIGHTS.write_text(json.dumps({"weights": w, "updated": datetime.now().isoformat(), **note}, indent=2))
    with WEIGHTS_HISTORY.open("a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(), "weights": w, **note}) + "\n")


def load_journal() -> list[dict]:
    if not PICKS.exists():
        return []
    return [json.loads(l) for l in PICKS.read_text().splitlines() if l.strip()]


def save_journal(rows: list[dict]):
    JOURNAL_DIR.mkdir(exist_ok=True)
    PICKS.write_text("".join(json.dumps(r) + "\n" for r in rows))


def score_from(features: dict, weights: dict) -> int:
    s = sum(weights.get(k, 0) * v for k, v in features.items())
    return int(max(0, min(100, round(s))))


def record_picks(day: date, built_at: datetime, entries: list[dict]):
    """Replace today's journal entries with the latest build's (the last pre-open build wins)."""
    rows = load_journal()
    todays = [r for r in rows if r["date"] == day.isoformat()]

    def pre_open(ts: datetime) -> bool:
        return ts.hour * 60 + ts.minute < 570

    if todays and not pre_open(built_at) and any(pre_open(datetime.fromisoformat(r["built_at"])) for r in todays):
        return  # keep the pre-open prediction; later builds would grade with hindsight
    rows = [r for r in rows if r["date"] != day.isoformat()]
    for e in entries:
        rows.append({"date": day.isoformat(), "built_at": built_at.isoformat(), "status": "pending", **e})
    save_journal(rows)


# ---------------------------------------------------------------- grading

def _simulate(bars: pd.DataFrame, trigger: float, stop: float) -> dict:
    entry = None
    for ts, b in bars.iterrows():
        if entry is None:
            if b["Low"] <= stop and b["High"] < trigger:
                return {"outcome": "invalidated before entry", "r": None}
            if b["High"] >= trigger:
                entry = max(trigger, float(b["Open"]))
                if b["Low"] <= stop:          # same-bar stop: assume the worst
                    return {"outcome": "stopped out", "r": (stop - entry) / (entry - stop), "entry": entry}
        elif b["Low"] <= stop:
            return {"outcome": "stopped out", "r": (stop - entry) / (entry - stop), "entry": entry,
                    "exit_time": ts.strftime("%H:%M")}
    if entry is None:
        return {"outcome": "never triggered", "r": None}
    exit_px = float(bars["Close"].iloc[-1])
    return {"outcome": "win" if exit_px > entry else "loss", "r": (exit_px - entry) / (entry - stop),
            "entry": entry, "exit": exit_px}


def grade_pending(today: date, et_tz) -> list[dict]:
    """Grade every pending entry from before today. Returns the newly graded rows."""
    rows = load_journal()
    pending = [r for r in rows if r["status"] == "pending" and r["date"] < today.isoformat()]
    if not pending:
        return []
    graded = []
    for day in sorted({r["date"] for r in pending}):
        d = date.fromisoformat(day)
        day_rows = [r for r in pending if r["date"] == day]
        if (today - d).days > MAX_EVAL_AGE_DAYS:
            for r in day_rows:
                r["status"] = "expired"
            continue
        tickers = sorted({r["ticker"] for r in day_rows})
        try:
            df = yf.download(tickers, start=d, end=d + timedelta(days=1), interval="5m", group_by="ticker",
                             auto_adjust=False, prepost=False, progress=False, threads=True)
        except Exception as e:
            print(f"  grading {day} failed: {e}")
            continue
        for r in day_rows:
            try:
                sub = df[r["ticker"]] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
            except KeyError:
                sub = pd.DataFrame()
            if sub.empty:
                r["status"] = "no data"
                continue
            idx = sub.index if sub.index.tz is not None else sub.index.tz_localize("UTC")
            sub = sub.set_axis(idx.tz_convert(et_tz))
            start = max(datetime.fromisoformat(r["built_at"]), sub.index[0].to_pydatetime())
            session = sub[(sub.index >= start) & ([t.hour * 60 + t.minute < 960 for t in sub.index])]
            if session.empty:
                r["status"] = "no data"
                continue
            res = _simulate(session, r["trigger"], r["stop"])
            r.update(res)
            r["open_to_close_pct"] = (float(session["Close"].iloc[-1]) / float(session["Open"].iloc[0]) - 1) * 100
            r["status"] = "graded"
            graded.append(r)
    save_journal(rows)
    return graded


# ---------------------------------------------------------------- learning

def learn(graded: list[dict]) -> dict:
    """One bounded update per graded day. Returns a summary for the report."""
    weights = load_weights()
    summary = {"days": [], "changes": {}, "wrong": []}
    for day in sorted({r["date"] for r in graded}):
        trades = [r for r in graded if r["date"] == day and r.get("r") is not None]
        if len(trades) < MIN_TRADES_TO_LEARN:
            summary["days"].append({"date": day, "trades": len(trades), "learned": False})
            continue
        grad = {k: 0.0 for k in weights}
        for t in trades:
            p = t["score"] / 100
            y = 1.0 if t["r"] > 0 else 0.0
            err = y - p
            boost = WRONG_BOOST if abs(err) >= WRONG_GAP else 1.0
            if boost > 1:
                summary["wrong"].append({"date": day, "ticker": t["ticker"], "score": t["score"],
                                         "outcome": t["outcome"], "r": t["r"]})
            for k, v in t["features"].items():
                if k in grad:
                    grad[k] += LR * boost * err * v
        before = dict(weights)
        for k, g in grad.items():
            step = max(-MAX_DAILY_CHANGE, min(MAX_DAILY_CHANGE, g / len(trades)))
            weights[k] = round(max(WEIGHT_BOUNDS[0], min(WEIGHT_BOUNDS[1], weights[k] + step)), 2)
        for k in weights:
            if abs(weights[k] - before[k]) >= 0.05:
                old = summary["changes"].get(k, (before[k], None))[0]
                summary["changes"][k] = (old, weights[k])
        summary["days"].append({"date": day, "trades": len(trades), "learned": True})
    if any(d["learned"] for d in summary["days"]):
        save_weights(weights, {"learned_from": [d["date"] for d in summary["days"] if d["learned"]]})
    summary["weights"] = weights
    return summary


def run_learning_cycle(today: date, et_tz) -> dict:
    graded = grade_pending(today, et_tz)
    summary = learn(graded) if graded else {"days": [], "changes": {}, "wrong": [], "weights": load_weights()}
    summary["graded"] = graded
    return summary


# ---------------------------------------------------------------- report

def _stats(rows: list[dict]) -> dict:
    trades = [r for r in rows if r.get("r") is not None]
    n = len(trades)
    return {"setups": len(rows), "trades": n,
            "win_rate": sum(r["r"] > 0 for r in trades) / n * 100 if n else None,
            "avg_r": sum(r["r"] for r in trades) / n if n else None}


def report_html(summary: dict, esc, fnum) -> str:
    all_graded = [r for r in load_journal() if r["status"] == "graded"]
    if not all_graded:
        return ("<div class=card><p>No graded setups yet. Today's setups are journaled and will be graded "
                "against real prices on the next run. The model starts adjusting once a day has at least "
                f"{MIN_TRADES_TO_LEARN} triggered trades.</p></div>")
    last_day = max(r["date"] for r in all_graded)
    last = sorted([r for r in all_graded if r["date"] == last_day], key=lambda r: -r["score"])

    def cls(x):
        return "" if x is None else "up" if x > 0 else "down"

    rows = "".join(
        f"<tr><td><b>{esc(r['ticker'])}</b></td><td>{r['score']}</td><td class=l>{esc(r['outcome'])}</td>"
        f"<td class='{cls(r.get('r'))}'>{fnum(r.get('r'), 2, sign=True)}{'R' if r.get('r') is not None else ''}</td>"
        f"<td class='{cls(r.get('open_to_close_pct'))}'>{fnum(r.get('open_to_close_pct'), 1, pct=True, sign=True)}</td>"
        f"<td>{fnum(r['trigger'])}</td><td>{fnum(r['stop'])}</td></tr>" for r in last)
    s_last = _stats(last)
    card = (f"<div class='card scroll'><div class=section-label>Report card: setups from {last_day}</div>"
            f"<p class=sub>{s_last['setups']} setups, {s_last['trades']} triggered · win rate "
            f"{fnum(s_last['win_rate'], 0, pct=True)} · average {fnum(s_last['avg_r'], 2, sign=True)}R</p>"
            "<table><tr><th>Ticker</th><th>Score</th><th class=l>Outcome</th><th>Result</th><th>Open→close</th>"
            f"<th>Trigger</th><th>Invalid.</th></tr>{rows}</table></div>")

    buckets = [("Score ≥ 70", lambda s: s >= 70), ("50–69", lambda s: 50 <= s < 70), ("< 50", lambda s: s < 50)]
    brow = ""
    for label, f in buckets:
        st = _stats([r for r in all_graded if f(r["score"])])
        brow += (f"<tr><td>{label}</td><td>{st['setups']}</td><td>{st['trades']}</td>"
                 f"<td>{fnum(st['win_rate'], 0, pct=True)}</td><td class='{cls(st['avg_r'])}'>"
                 f"{fnum(st['avg_r'], 2, sign=True)}</td></tr>")
    hi = _stats([r for r in all_graded if r["score"] >= 70])
    lo = _stats([r for r in all_graded if r["score"] < 70])
    verdict = ""
    if hi["trades"] >= 10 and lo["trades"] >= 10 and (hi["avg_r"] or 0) <= (lo["avg_r"] or 0):
        verdict = ("<p class=warn><b>High scores are not beating low scores so far.</b> The model has no proven edge "
                   "yet. Treat the ranking as unvalidated.</p>")
    elif hi["trades"] + lo["trades"] < 30:
        verdict = (f"<p class=sub>Only {hi['trades'] + lo['trades']} graded trades so far. Too few to tell skill "
                   "from luck (aim for 50+).</p>")
    calib = (f"<div class='card scroll'><div class=section-label>All-time calibration (since journaling began)</div>"
             f"{verdict}<table><tr><th>Score band</th><th>Setups</th><th>Triggered</th><th>Win rate</th>"
             f"<th>Avg R</th></tr>{brow}</table></div>")

    ch = summary.get("changes") or {}
    if ch:
        crow = "".join(
            f"<tr><td class=l>{esc(FEATURE_LABELS.get(k, k))}</td><td>{fnum(a, 1)}</td><td>{fnum(b, 1)}</td>"
            f"<td class='{cls(b - a)}'>{fnum(b - a, 1, sign=True)}</td></tr>"
            for k, (a, b) in sorted(ch.items(), key=lambda kv: -abs(kv[1][1] - kv[1][0])))
        wrong = summary.get("wrong") or []
        wtxt = ("<p class=sub>Completely wrong calls (larger correction applied): " + ", ".join(
            f"{esc(w['ticker'])} (score {w['score']}, {esc(w['outcome'])}, {w['r']:+.2f}R)" for w in wrong)
                + "</p>") if wrong else ""
        learned = (f"<div class='card scroll'><div class=section-label>What the model learned this run</div>{wtxt}"
                   f"<table><tr><th class=l>Feature</th><th>Old weight</th><th>New weight</th><th>Change</th></tr>"
                   f"{crow}</table></div>")
    else:
        days = summary.get("days") or []
        why = (f"Graded days had fewer than {MIN_TRADES_TO_LEARN} triggered trades, so no update was made."
               if days else "No new days to grade this run.")
        learned = f"<div class=card><div class=section-label>What the model learned this run</div><p class=sub>{why}</p></div>"

    w = summary.get("weights") or load_weights()
    wrow = "".join(
        f"<tr><td class=l>{esc(FEATURE_LABELS.get(k, k))}</td><td>{fnum(DEFAULT_WEIGHTS[k], 0)}</td>"
        f"<td><b>{fnum(v, 1)}</b></td></tr>" for k, v in sorted(w.items(), key=lambda kv: -kv[1]))
    weights = (f"<div class='card scroll'><div class=section-label>Current weights (start → now)</div>"
               f"<table><tr><th class=l>Feature</th><th>Start</th><th>Now</th></tr>{wrow}</table></div>")
    return f"<div class=grid>{card}{calib}{learned}{weights}</div>"
