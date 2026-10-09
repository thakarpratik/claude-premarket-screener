"""Event-driven backtests of the swing and intraday pullback rules.

Safeguards:
- No look-ahead: the scanner runs on bars [0..t] only; swing pivots are used only once confirmed; orders are
  placed after bar t closes and can fill no earlier than bar t+1.
- Conservative fills: buy-stop fills at max(open, trigger) + slippage; stops fill at min(open, stop) - slippage
  (gaps through the stop are not forgiven); if a bar touches both stop and target, the stop is assumed first.
- Costs: per-share slippage on every fill plus commission per order.
- Out-of-sample: the first `in_sample_pct` of the date range is in-sample, the rest out-of-sample; both are reported.
- Regimes: each trade is tagged with the S&P 500 proxy's regime (bull/bear/sideways) on the signal date.
- Survivorship bias: the tested symbols are today's listings; delisted losers are absent, which flatters results.
Results on synthetic demo data are labelled as such and are not evidence of an edge."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..market_calendar import ET, is_trading_day
from ..scanner.intraday import analyze_intraday
from ..scanner.swing import PIVOT_K, analyze_swing


@dataclass
class BTParams:
    style: str = "swing"
    min_reward_risk: float = 2.0
    entry_window_bars: int = 3  # swing: sessions; intraday: 5-min bars
    max_hold_bars: int = 10  # swing sessions
    slippage_per_share: float = 0.02
    commission_per_order: float = 0.0
    risk_pct: float = 0.5
    account_equity: float = 25_000
    in_sample_pct: float = 70.0
    require_market_uptrend: bool = False
    statuses: list[str] = field(default_factory=lambda: ["Approaching Entry", "Triggered"])
    scan_every_minutes: int = 5  # intraday


@dataclass
class BTTrade:
    symbol: str
    signal_time: str
    entry_time: str
    entry: float
    exit_time: str
    exit: float
    exit_reason: str
    stop: float
    target1: float
    shares_frac: float  # fraction-weighted average exit handled via `exit`
    r_multiple: float
    gross_r: float
    return_pct: float
    regime: str
    period: str
    setup_type: str | None
    bars_held: int


def _regime_series(spy: pd.DataFrame | None) -> pd.Series | None:
    if spy is None or len(spy) < 60:
        return None
    f = ind.add_daily_indicators(spy)
    up = (f["close"] > f["sma50"]) & (f["sma50"] > f["sma50"].shift(10))
    down = (f["close"] < f["sma50"]) & (f["sma50"] < f["sma50"].shift(10))
    return pd.Series(np.where(up, "bull", np.where(down, "bear", "sideways")), index=f.index.normalize())


def _regime_at(reg: pd.Series | None, ts) -> str:
    if reg is None:
        return "unknown"
    d = pd.Timestamp(ts).tz_convert(ET).normalize()
    s = reg[reg.index <= d]
    return str(s.iloc[-1]) if len(s) else "unknown"


def _simulate_exit(bars: pd.DataFrame, start: int, entry: float, stop: float, t1: float, t2: float | None,
                   slip: float, max_hold: int | None, eod_index: int | None = None) -> tuple[float, int, str]:
    """Walk bars from `start` (the fill bar). Half off at T1 (stop to breakeven) when T2 exists. Returns
    (average exit price, exit bar index, reason)."""
    o, h, l_, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    half_done, exits, cur_stop = False, [], stop
    last = len(bars) - 1 if eod_index is None else min(eod_index, len(bars) - 1)
    for i in range(start, last + 1):
        if l_[i] <= cur_stop:  # stop first when ambiguous
            px = (min(o[i], cur_stop) if i > start else cur_stop) - slip
            rem = 0.5 if half_done else 1.0
            exits.append((rem, px))
            return sum(w * p for w, p in exits) / sum(w for w, _ in exits), i, ("breakeven stop" if half_done else "stop")
        if not half_done and h[i] >= t1:
            px = max(o[i], t1) if i > start else t1
            if t2 is None:
                return px - slip, i, "target1"
            exits.append((0.5, px - slip))
            half_done, cur_stop = True, max(cur_stop, entry)
        if half_done and t2 is not None and h[i] >= t2:
            exits.append((0.5, max(o[i], t2) - slip))
            return sum(w * p for w, p in exits) / 1.0, i, "target2"
        if max_hold is not None and i - start + 1 >= max_hold:
            rem = 0.5 if half_done else 1.0
            exits.append((rem, c[i] - slip))
            return sum(w * p for w, p in exits) / sum(w for w, _ in exits), i, "time exit"
    rem = 0.5 if half_done else 1.0
    exits.append((rem, c[last] - slip))
    return sum(w * p for w, p in exits) / sum(w for w, _ in exits), last, ("end of day" if eod_index is not None
                                                                           else "end of data")


def _commission_r(p: BTParams) -> float:
    """Round-trip commission expressed in R for a position sized to risk `risk_pct` of equity."""
    budget = p.account_equity * p.risk_pct / 100
    return 2 * p.commission_per_order / budget if budget > 0 else 0.0


def backtest_swing_symbol(sym: str, daily: pd.DataFrame, p: BTParams, reg: pd.Series | None,
                          bench: pd.DataFrame | None, split_ts) -> tuple[list[BTTrade], int]:
    f = ind.add_daily_indicators(daily)
    piv = ind.pivots(f, PIVOT_K)
    trades, signals = [], 0
    t, n = 60, len(f)
    while t < n - 1:
        a = analyze_swing(f.iloc[:t + 1], sym, bench=bench, pivots=piv, _check_invalidation=False)
        ok = (a.plan and a.status in p.statuses and (a.plan.reward_risk or 0) >= p.min_reward_risk
              and (not p.require_market_uptrend or _regime_at(reg, f.index[t]) == "bull"))
        if not ok:
            t += 1
            continue
        signals += 1
        plan = a.plan
        fill_i, fill_px = None, None
        if a.status == "Triggered":
            j = t + 1
            if f["open"].iloc[j] > plan.stop:
                fill_i, fill_px = j, float(f["open"].iloc[j]) + p.slippage_per_share
        else:
            for j in range(t + 1, min(n, t + 1 + p.entry_window_bars)):
                if f["open"].iloc[j] <= plan.stop or f["low"].iloc[j] <= plan.stop and f["high"].iloc[j] < plan.trigger_price:
                    break  # invalidated before triggering
                if f["high"].iloc[j] >= plan.trigger_price:
                    fill_i, fill_px = j, max(float(f["open"].iloc[j]), plan.trigger_price) + p.slippage_per_share
                    break
        if fill_i is None:
            t += 1
            continue
        assert fill_i > t, "look-ahead guard: fills must come after the signal bar"
        exit_px, exit_i, reason = _simulate_exit(f, fill_i, fill_px, plan.stop, plan.target1, plan.target2,
                                                 p.slippage_per_share, p.max_hold_bars)
        risk = fill_px - plan.stop
        if risk <= 0:
            t = exit_i + 1
            continue
        comm = 2 * p.commission_per_order
        comm_r = _commission_r(p)
        net_r = (exit_px - fill_px) / risk - comm_r
        gross_r = (exit_px - fill_px + 2 * p.slippage_per_share) / risk
        trades.append(BTTrade(
            symbol=sym, signal_time=f.index[t].isoformat(), entry_time=f.index[fill_i].isoformat(), entry=round(fill_px, 4),
            exit_time=f.index[exit_i].isoformat(), exit=round(exit_px, 4), exit_reason=reason, stop=plan.stop,
            target1=plan.target1, shares_frac=1.0, r_multiple=round(net_r, 4), gross_r=round(gross_r, 4),
            return_pct=round((exit_px / fill_px - 1) * 100 - comm / fill_px * 100, 4), regime=_regime_at(reg, f.index[t]),
            period="in_sample" if f.index[t] < split_ts else "out_of_sample", setup_type=a.setup_type,
            bars_held=exit_i - fill_i + 1))
        t = exit_i + 1  # one position per symbol at a time
    return trades, signals


def backtest_intraday_day(sym: str, bars1: pd.DataFrame, daily: pd.DataFrame, p: BTParams, spy1, reg, split_ts,
                          or_minutes: int = 15) -> tuple[list[BTTrade], int]:
    reg_bars = bars1[(bars1.index.time >= time(9, 30)) & (bars1.index.time < time(16, 0))]
    if len(reg_bars) < 60:
        return [], 0
    trades, signals = [], 0
    eod = int(np.searchsorted(reg_bars.index.time, time(15, 55)))
    i = or_minutes + 15
    while i < eod - 5:
        cut = reg_bars.index[i]
        hist = bars1[bars1.index < cut]
        a = analyze_intraday(hist, sym, daily, or_minutes=or_minutes,
                             spy1=spy1[spy1.index < cut] if spy1 is not None else None, _check_invalidation=False)
        if not (a.plan and a.status in p.statuses and (a.plan.reward_risk or 0) >= p.min_reward_risk):
            i += p.scan_every_minutes
            continue
        signals += 1
        plan = a.plan
        fill_i = fill_px = None
        if a.status == "Triggered":
            fill_i, fill_px = i, float(reg_bars["open"].iloc[i]) + p.slippage_per_share
        else:
            for j in range(i, min(eod, i + 5 * p.entry_window_bars)):
                if reg_bars["low"].iloc[j] <= plan.stop and reg_bars["high"].iloc[j] < plan.trigger_price:
                    break
                if reg_bars["high"].iloc[j] >= plan.trigger_price:
                    fill_i, fill_px = j, max(float(reg_bars["open"].iloc[j]), plan.trigger_price) + p.slippage_per_share
                    break
        if fill_i is None or fill_px <= plan.stop:
            i += p.scan_every_minutes
            continue
        assert reg_bars.index[fill_i] >= cut, "look-ahead guard"
        exit_px, exit_i, reason = _simulate_exit(reg_bars, fill_i, fill_px, plan.stop, plan.target1, plan.target2,
                                                 p.slippage_per_share, None, eod_index=eod)
        risk = fill_px - plan.stop
        comm_pct = 2 * p.commission_per_order / fill_px * 100 if fill_px else 0
        trades.append(BTTrade(
            symbol=sym, signal_time=cut.isoformat(), entry_time=reg_bars.index[fill_i].isoformat(), entry=round(fill_px, 4),
            exit_time=reg_bars.index[exit_i].isoformat(), exit=round(exit_px, 4), exit_reason=reason, stop=plan.stop,
            target1=plan.target1, shares_frac=1.0, r_multiple=round((exit_px - fill_px) / risk - _commission_r(p), 4),
            gross_r=round((exit_px - fill_px + 2 * p.slippage_per_share) / risk, 4),
            return_pct=round((exit_px / fill_px - 1) * 100 - comm_pct, 4), regime=_regime_at(reg, cut),
            period="in_sample" if cut < split_ts else "out_of_sample", setup_type=a.setup_type,
            bars_held=exit_i - fill_i + 1))
        i = exit_i + 1
    return trades, signals


def metrics(trades: list[BTTrade], risk_pct: float, commission_r: float = 0.0) -> dict:
    if not trades:
        return {"trades": 0}
    tr = sorted(trades, key=lambda x: x.exit_time)
    r = np.array([t.r_multiple for t in tr])
    g = np.array([t.gross_r for t in tr])
    pct = np.array([t.return_pct for t in tr])
    wins, losses = r[r > 0], r[r <= 0]
    eq = np.cumprod(1 + r * risk_pct / 100)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    dd = ((peak - eq) / peak).max() * 100
    gw, gl = wins.sum(), -losses.sum()
    return {
        "trades": int(len(r)), "win_rate": float((r > 0).mean() * 100),
        "avg_win_r": float(wins.mean()) if len(wins) else 0.0, "avg_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "avg_win_pct": float(pct[r > 0].mean()) if len(wins) else 0.0,
        "avg_loss_pct": float(pct[r <= 0].mean()) if len(losses) else 0.0,
        "expectancy_r": float(r.mean()), "expectancy_pct": float(pct.mean()),
        "expectancy_r_before_costs": float(g.mean()),
        "profit_factor": float(gw / gl) if gl > 0 else None,
        "max_drawdown_pct": float(dd), "total_return_pct": float((eq[-1] - 1) * 100),
        "avg_bars_held": float(np.mean([t.bars_held for t in tr])),
    }


def _group(trades, key, risk_pct):
    out = {}
    for k in sorted({getattr(t, key) for t in trades}):
        out[k] = metrics([t for t in trades if getattr(t, key) == k], risk_pct)
    return out


def run_backtest(hub, symbols: list[str], p: BTParams, start: date, end: date, *, days_intraday: int = 10,
                 or_minutes: int = 15) -> dict:
    synthetic = hub.synthetic
    spy = hub.market.daily_bars("SPY", start - timedelta(days=400), end)
    spy_c = spy[["open", "high", "low", "close", "volume"]]
    if spy.attrs.get("partial_last_bar"):
        spy_c = spy_c.iloc[:-1]
    reg = _regime_series(spy_c)
    trades, signals, errors = [], 0, []
    if p.style == "swing":
        span = (pd.Timestamp(start, tz=ET), pd.Timestamp(end, tz=ET))
        split_ts = span[0] + (span[1] - span[0]) * (p.in_sample_pct / 100)
        for sym in symbols:
            try:
                d = hub.market.daily_bars(sym, start - timedelta(days=120), end)
                if d.attrs.get("partial_last_bar"):
                    d = d.iloc[:-1]  # never test on a forming bar
                d = d[d.index >= pd.Timestamp(start - timedelta(days=120), tz=ET)]
                tr, sg = backtest_swing_symbol(sym, d, p, reg, spy_c, split_ts)
                trades += [t for t in tr if pd.Timestamp(t.signal_time) >= pd.Timestamp(start, tz=ET)]
                signals += sg
            except Exception as e:  # noqa: BLE001
                errors.append({"symbol": sym, "error": f"{type(e).__name__}: {e}"})
    else:
        today_ref = hub.market.now().date() if synthetic else datetime.now(ET).date()
        days, d = [], min(end, today_ref - timedelta(days=1))
        while len(days) < days_intraday:
            if is_trading_day(d):
                days.append(d)
            d -= timedelta(days=1)
            if (end - d).days > 60:
                break
        days = sorted(days)
        if not days:
            return {"ok": False, "error": "No completed sessions in range"}
        k = max(1, int(len(days) * p.in_sample_pct / 100))
        split_ts = pd.Timestamp(datetime.combine(days[min(k, len(days) - 1)], time(0, 0)), tz=ET)
        for sym in symbols:
            try:
                daily = ind.add_daily_indicators(hub.market.daily_bars(sym, days[0] - timedelta(days=120), days[-1]))
                for day in days:
                    b1 = hub.market.intraday_bars(sym, day, 1, extended=True)
                    s1 = hub.market.intraday_bars("SPY", day, 1, extended=True)
                    prior = daily[daily.index.date < day]
                    tr, sg = backtest_intraday_day(sym, b1[b1.index.time < time(16, 0)], prior, p, s1, reg, split_ts,
                                                   or_minutes)
                    trades += tr
                    signals += sg
            except Exception as e:  # noqa: BLE001
                errors.append({"symbol": sym, "error": f"{type(e).__name__}: {e}"})
    res = {
        "ok": True, "style": p.style, "params": asdict(p), "symbols": symbols, "start": start.isoformat(),
        "end": end.isoformat(), "synthetic": synthetic, "signals": signals,
        "overall": metrics(trades, p.risk_pct), "by_period": _group(trades, "period", p.risk_pct),
        "by_regime": _group(trades, "regime", p.risk_pct), "by_setup": _group(trades, "setup_type", p.risk_pct),
        "trades": [asdict(t) for t in sorted(trades, key=lambda t: t.entry_time)], "errors": errors,
        "safeguards": [
            "Signals use only bars up to the signal bar; swing pivots are used only after confirmation.",
            "Entries fill on later bars via buy-stop at max(open, trigger) + slippage; stop fills at min(open, stop) - slippage.",
            "When a bar touches both the stop and a target, the stop is assumed to fill first.",
            f"Slippage {p.slippage_per_share:.2f}/share per fill and {p.commission_per_order:.2f} commission per order included.",
            f"First {p.in_sample_pct:.0f}% of the period is in-sample; the remainder is out-of-sample.",
            "Survivorship bias: only currently listed symbols are tested; delisted names are missing.",
        ],
        "warnings": [],
    }
    if synthetic:
        res["warnings"].append("SYNTHETIC DEMO DATA — these results describe invented prices and say nothing about "
                               "real-market performance.")
    n = res["overall"].get("trades", 0)
    if n < 30:
        res["warnings"].append(f"Only {n} trades — too few to draw conclusions; treat all statistics as noise.")
    if p.style == "intraday":
        res["warnings"].append("Intraday relative volume uses a typical volume curve, and fills ignore queue position "
                               "and partial fills.")
    from .scan import jsonable
    return jsonable(res)
