"""Position sizing and risk checks. Pure functions; amounts in USD."""
from __future__ import annotations

import math

GAP_NOTE = ("Stop orders become market orders when touched and can fill well below the stop in fast markets or "
            "on overnight gaps; actual loss can exceed the planned risk.")


def position_size(*, equity: float, risk_pct: float, entry: float, stop: float, slippage_per_share: float = 0.0,
                  commission_per_trade: float = 0.0, max_position_pct: float = 100.0,
                  targets: list[float] | None = None, min_reward_risk: float = 2.0) -> dict:
    errors = []
    if equity <= 0:
        errors.append("Account equity must be positive")
    if not 0 < risk_pct <= 100:
        errors.append("Risk per trade must be between 0 and 100%")
    if entry <= 0 or stop <= 0:
        errors.append("Entry and stop must be positive")
    if stop >= entry:
        errors.append("Stop must be below the entry for a long trade")
    if slippage_per_share < 0 or commission_per_trade < 0:
        errors.append("Slippage and commission cannot be negative")
    if errors:
        return {"valid": False, "errors": errors}
    gross_rps = entry - stop
    eff_rps = gross_rps + 2 * slippage_per_share  # slip on the entry fill and on the stop fill
    budget = equity * risk_pct / 100
    fees = 2 * commission_per_trade
    by_risk = math.floor(max(0.0, budget - fees) / eff_rps)
    by_cap = math.floor(equity * max_position_pct / 100 / entry)
    shares = max(0, min(by_risk, by_cap))
    out = {
        "valid": True, "errors": [], "risk_budget": round(budget, 2), "risk_per_share": round(gross_rps, 4),
        "risk_per_share_with_slippage": round(eff_rps, 4), "shares": shares,
        "limited_by": "max position size" if by_cap < by_risk else "risk budget",
        "position_value": round(shares * entry, 2),
        "position_pct_of_equity": round(shares * entry / equity * 100, 2),
        "total_planned_risk": round(shares * eff_rps + (fees if shares else 0), 2),
        "planned_risk_pct": round((shares * eff_rps + (fees if shares else 0)) / equity * 100, 3),
        "targets": [], "warnings": [], "notes": [GAP_NOTE],
    }
    for i, t in enumerate(targets or []):
        if t is None:
            continue
        gross_rr = (t - entry) / gross_rps
        profit = shares * (t - entry - 2 * slippage_per_share) - (fees if shares else 0)
        out["targets"].append({
            "label": f"Target {i + 1}", "price": t, "reward_risk": round(gross_rr, 2),
            "net_profit": round(profit, 2),
            "net_reward_risk": round(profit / out["total_planned_risk"], 2) if out["total_planned_risk"] else None,
            "gain_pct": round((t / entry - 1) * 100, 2),
        })
    if shares == 0:
        out["warnings"].append("Risk budget is smaller than the risk of one share — skip this trade or widen the "
                               "account risk only if it fits your plan.")
    if out["targets"] and out["targets"][0]["reward_risk"] < min_reward_risk:
        out["warnings"].append(f"Reward-to-risk {out['targets'][0]['reward_risk']:.2f}:1 is below your "
                               f"{min_reward_risk:.1f}:1 minimum.")
    if out["limited_by"] == "max position size":
        out["warnings"].append("Share count capped by the maximum position size, so total risk is below budget.")
    return out


def daily_loss_status(realized_today: float, equity: float, limit_pct: float) -> dict:
    limit = equity * limit_pct / 100
    loss = -min(0.0, realized_today)
    reached = loss >= limit > 0
    return {
        "realized_today": round(realized_today, 2), "limit": round(limit, 2), "loss_today": round(loss, 2),
        "remaining": round(max(0.0, limit - loss), 2), "reached": reached,
        "message": ("Daily loss limit reached — stop trading for today. Do not increase size to win it back."
                    if reached else None),
    }
