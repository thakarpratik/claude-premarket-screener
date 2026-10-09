"""Alert evaluation with de-duplication. Alerts describe market conditions only; they never imply an order."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import Alert, AlertRule, AlertState, WatchlistItem

KINDS = {
    "entry_zone": "Entered the proposed entry zone",
    "trigger": "Bullish confirmation trigger",
    "invalidation": "Support / stop level broken — setup invalid",
    "target1": "First profit target reached",
    "target2": "Second profit target reached",
    "news": "Material news",
    "unusual_activity": "Unusual volume or price movement",
    "manipulation": "Manipulation-risk level increased",
    "event": "Earnings or scheduled event approaching",
    "price_cross": "Custom price level crossed",
}
NOT_AN_ORDER = "Alert only — no order has been placed."


def ensure_default_rules(db: Session, user_id: int):
    have = {r.kind for r in db.scalars(select(AlertRule).where(AlertRule.user_id == user_id, AlertRule.symbol.is_(None)))}
    for k in KINDS:
        if k != "price_cross" and k not in have:
            db.add(AlertRule(user_id=user_id, symbol=None, kind=k,
                             params={"event_days": 3} if k == "event" else {"rel_volume": 3.0} if k == "unusual_activity"
                             else {}, enabled=True))
    db.flush()


def conditions(card: dict, plan: dict | None, rule_params: dict) -> dict[str, tuple[bool, str, str]]:
    """kind -> (active, fingerprint, reason) for one scan card."""
    px = card.get("price")
    out: dict[str, tuple[bool, str, str]] = {}
    if plan and px is not None:
        lo, hi = plan["entry_zone_low"], plan["entry_zone_high"]
        out["entry_zone"] = (lo <= px <= hi, f"{lo}-{hi}",
                             f"Price {px:,.2f} is inside the {lo:,.2f}–{hi:,.2f} entry zone. Wait for the trigger: "
                             f"{plan['trigger_text']}")
        out["invalidation"] = (px < plan["stop"] or card["status"] == "Invalidated", str(plan["stop"]),
                               f"Price {px:,.2f} vs stop/invalidation {plan['stop']:,.2f}.")
        out["target1"] = (px >= plan["target1"], str(plan["target1"]),
                          f"Price {px:,.2f} reached target 1 ({plan['target1_text']}).")
        if plan.get("target2"):
            out["target2"] = (px >= plan["target2"], str(plan["target2"]),
                              f"Price {px:,.2f} reached target 2 ({plan['target2_text']}).")
    out["trigger"] = (card["status"] == "Triggered", card.get("bar_as_of") or "",
                      (plan or {}).get("trigger_text", "Trigger condition met."))
    material = [n for n in card.get("news", []) if n["impact"] in ("high", "medium")
                and n["verification"] in ("verified", "established") and n["sentiment"] != "neutral"]
    out["news"] = (bool(material), ",".join(sorted(n["id"] for n in material)),
                   "; ".join(f"{n['sentiment']}: {n['headline']} ({n['publisher']}, {n['published_at'][:16]})"
                             for n in material[:3]))
    m = card.get("metrics", {})
    rv = m.get("rel_volume") or m.get("relative_volume_est")
    chg = m.get("day_change_pct") or m.get("change_pct")
    atrp = m.get("atr_pct")
    thr = float(rule_params.get("unusual_activity", {}).get("rel_volume", 3.0))
    unusual = (rv is not None and rv >= thr) or (chg is not None and atrp and abs(chg) >= 2.5 * atrp)
    out["unusual_activity"] = (bool(unusual), f"{round(rv or 0)}",
                               f"Relative volume {rv or 0:.1f}x, change {chg or 0:+.1f}%.")
    lvl = card.get("manipulation", {}).get("level")
    out["manipulation"] = (lvl in ("elevated", "high"), lvl or "",
                           f"Manipulation-risk score {card['manipulation'].get('score')} ({lvl}): " +
                           "; ".join(f["label"] for f in card["manipulation"].get("flags", [])[:3]))
    days = int(rule_params.get("event", {}).get("event_days", 3))
    ne = (card.get("events") or {}).get("next_earnings")
    out["event"] = (bool(ne and ne["trading_days_away"] <= days), ne["date"] if ne else "",
                    f"Earnings on {ne['date']} ({ne['trading_days_away']} trading days)." if ne else "")
    return out


def evaluate_user(db: Session, user_id: int, scan: dict) -> list[Alert]:
    if not scan.get("ok"):
        return []
    ensure_default_rules(db, user_id)
    rules = list(db.scalars(select(AlertRule).where(AlertRule.user_id == user_id)))
    enabled = {r.kind for r in rules if r.enabled and r.symbol is None}
    params = {r.kind: r.params or {} for r in rules if r.symbol is None}
    custom = [r for r in rules if r.symbol and r.enabled]
    items = list(db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user_id)))
    created = []
    states = {(s.symbol, s.style, s.kind): s for s in
              db.scalars(select(AlertState).where(AlertState.user_id == user_id))}

    def fire(sym, style, kind, active, fp, reason, card):
        st = states.get((sym, style, kind))
        if st is None:
            st = AlertState(user_id=user_id, symbol=sym, style=style, kind=kind, active=False, fingerprint=None)
            db.add(st)
            states[(sym, style, kind)] = st
        should = active and (not st.active or (kind == "news" and fp != st.fingerprint))
        st.active, st.fingerprint = active, fp if active else None
        if not should:
            return
        a = Alert(user_id=user_id, symbol=sym, style=style, kind=kind, title=f"{sym}: {KINDS[kind]}",
                  message=f"{reason} {NOT_AN_ORDER}", price=card.get("price"), price_time=card.get("price_time"),
                  link=f"/research/{sym}?style={style}", synthetic=bool(scan.get("synthetic")))
        db.add(a)
        created.append(a)

    for it in items:
        card = (scan.get(it.style) or {}).get("all", {}).get(it.symbol)
        if not card:
            continue
        plan = it.plan_snapshot or card.get("plan")
        conds = conditions(card, plan, params)
        for kind, (active, fp, reason) in conds.items():
            if kind in enabled:
                fire(it.symbol, it.style, kind, active, fp, reason, card)
        for r in custom:
            if r.symbol != it.symbol or r.kind != "price_cross" or card.get("price") is None:
                continue
            px = card["price"]
            above, below = r.params.get("above"), r.params.get("below")
            if above is not None:
                fire(it.symbol, it.style, "price_cross", px >= float(above), f"above{above}",
                     f"Price {px:,.2f} crossed above {float(above):,.2f}.", card)
            if below is not None:
                fire(it.symbol, it.style, "price_cross", px <= float(below), f"below{below}",
                     f"Price {px:,.2f} crossed below {float(below):,.2f}.", card)
        it.last_status, it.last_price = card["status"], card.get("price")
        it.last_checked_at = datetime.now(timezone.utc)
    db.flush()
    return created
