"""Trade journal statistics and paper-trade monitoring. Paper and actual results are never combined."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone

from ..market_calendar import ET


def trade_pnl(t) -> float | None:
    if t.exit_price is None:
        return None
    return (t.exit_price - t.entry_price) * t.quantity - (t.fees or 0)


def trade_r(t) -> float | None:
    if t.exit_price is None or t.stop is None or t.entry_price <= t.stop:
        return None
    return (t.exit_price - t.entry_price) / (t.entry_price - t.stop)


def performance(pnls: list[float], rs: list[float | None] | None = None) -> dict:
    n = len(pnls)
    if n == 0:
        return {"trades": 0}
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    eq, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    rv = [r for r in (rs or []) if r is not None]
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "win_rate": len(wins) / n * 100,
        "avg_win": (gross_win / len(wins)) if wins else 0.0, "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "expectancy": sum(pnls) / n, "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "net_pnl": sum(pnls), "max_drawdown": mdd,
        "avg_r": (sum(rv) / len(rv)) if rv else None, "r_sample": len(rv),
    }


def journal_stats(trades: list) -> dict:
    out = {}
    for mode in ("paper", "actual"):
        closed = sorted([t for t in trades if t.mode == mode and t.status == "closed" and t.exit_price is not None],
                        key=lambda t: t.exit_time or t.entry_time)
        overall = performance([trade_pnl(t) for t in closed], [trade_r(t) for t in closed])
        by = defaultdict(list)
        for t in closed:
            by[(t.style, t.strategy or "unspecified")].append(t)
        out[mode] = {
            "overall": overall,
            "by_strategy": [{"style": k[0], "strategy": k[1],
                             **performance([trade_pnl(t) for t in v], [trade_r(t) for t in v])} for k, v in by.items()],
            "open_positions": sum(1 for t in trades if t.mode == mode and t.status == "open"),
        }
    out["note"] = ("Paper results are simulated fills at observed prices and are reported separately from actual "
                   "trades. Past results do not guarantee future performance.")
    return out


def realized_today(trades: list, today) -> float:
    tot = 0.0
    for t in trades:
        if t.status == "closed" and t.exit_time and t.exit_time.astimezone(ET).date() == today:
            tot += trade_pnl(t) or 0.0
    return tot


def update_paper_trade(t, price: float, price_time: datetime, now_et_: datetime) -> str | None:
    """Close or adjust an open paper trade based on an observed price. Returns a note when something changed."""
    if t.status != "open" or t.mode != "paper":
        return None
    snap = dict(t.setup_snapshot or {})
    if t.stop is not None and price <= t.stop:
        _close(t, price, price_time, "stop", snap)
        return f"Stopped at observed price {price:,.2f} (stop {t.stop:,.2f}); a gap can fill below the stop."
    if t.target2 and price >= t.target2:
        _close(t, price, price_time, "target2", snap)
        return f"Target 2 reached at {price:,.2f}."
    if t.target1 and price >= t.target1:
        if t.target2 and not snap.get("t1_hit"):
            snap["t1_hit"] = True
            t.stop = max(t.stop or 0, t.entry_price)
            t.setup_snapshot = snap
            return f"Target 1 reached at {price:,.2f}; paper stop moved to breakeven {t.entry_price:,.2f}."
        if not t.target2:
            _close(t, price, price_time, "target1", snap)
            return f"Target 1 reached at {price:,.2f}."
    if t.style == "intraday" and now_et_.time() >= time(15, 55):
        _close(t, price, price_time, "end_of_day", snap)
        return f"Intraday paper trade closed at the end of the session ({price:,.2f})."
    return None


def _close(t, price, price_time, reason, snap):
    t.exit_price, t.exit_time, t.exit_reason, t.status = price, price_time.astimezone(timezone.utc), reason, "closed"
    t.setup_snapshot = snap
