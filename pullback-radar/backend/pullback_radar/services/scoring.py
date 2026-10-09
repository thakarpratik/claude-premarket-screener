"""Transparent setup-quality score (0-100), hard exclusions, and plain-language explanations.

The score is a setup-quality rating, not a probability of profit. Weights are configurable and should be
validated with the backtester; they are not assumed optimal."""
from __future__ import annotations

import math

from ..config import ScanSettings
from ..scanner.common import SetupAnalysis

COMPONENT_LABELS = {
    "technical": "Technical setup quality", "trend_rs": "Trend & relative strength",
    "entry": "Entry quality & support proximity", "volume_liquidity": "Volume & liquidity",
    "news": "News & verified catalysts", "market_alignment": "Market & sector alignment",
    "sentiment": "Sentiment quality",
}


def _ratio(sigs) -> float | None:
    tot = sum(s.weight for s in sigs)
    return (sum(s.weight for s in sigs if s.passed) / tot) if tot else None


def _liquidity_points(dollar_vol: float | None) -> float:
    if not dollar_vol or dollar_vol <= 0:
        return 0.0
    # 5M -> 20, 20M -> 46, 100M -> 76, 500M+ -> 100 (log scale)
    return max(0.0, min(100.0, 20 + 44 * math.log10(dollar_vol / 5e6)))


def components(a: SetupAnalysis, *, news_score: float, regime: str, sector_ok: bool | None,
               social_score: float | None, spread_pct: float | None) -> dict[str, float]:
    sig = [s for s in a.signals if s.group != "breakdown"]
    tech = _ratio([s for s in sig if s.group in ("pullback", "confirmation", "structure")]) or 0
    tech_score = tech * 85 + (15 if a.status == "Triggered" else 8 if a.technical_state == "stabilizing" else 0)
    trend = _ratio([s for s in sig if s.group in ("trend", "relative_strength")]) or 0
    plan = a.plan
    atr = a.metrics.get("atr") or a.metrics.get("atr_5m")
    entry_score = 0.0
    if plan and atr and a.price:
        rr = plan.reward_risk or 0
        rr_pts = 100 if rr >= 3 else 75 + (rr - 2) * 25 if rr >= 2 else 30 + (rr - 1) * 45 if rr >= 1 else max(0, rr * 30)
        dist = abs(a.price - plan.entry_price) / atr
        prox = max(0.0, 100 - dist * 33)
        entry_score = 0.5 * rr_pts + 0.5 * prox
    vol = _ratio([s for s in sig if s.group == "volume"])
    dv = a.metrics.get("dollar_volume_20d")
    liq = _liquidity_points(dv)
    if spread_pct is not None:
        liq -= min(40, max(0, (spread_pct - 0.1) * 40))
    vl = 0.6 * ((vol or 0) * 100) + 0.4 * max(0, liq)
    mkt = {"bullish": 85, "mixed": 55, "bearish": 20}.get(regime, 50)
    sec = 85 if sector_ok else 35 if sector_ok is False else 50
    intr = [s for s in sig if s.key == "market_align"]
    align = (mkt + sec) / 2 if not intr else (mkt + sec + (85 if intr[0].passed else 25)) / 3
    return {"technical": round(tech_score, 1), "trend_rs": round(trend * 100, 1), "entry": round(entry_score, 1),
            "volume_liquidity": round(vl, 1), "news": round(news_score, 1), "market_alignment": round(align, 1),
            "sentiment": round(50.0 if social_score is None else social_score, 1)}


def social_score(social, manip_flags: list[dict]) -> tuple[float | None, str]:
    if social is None:
        return None, "Social sentiment unavailable from configured sources (neutral 50 used)."
    if any(f["key"] in ("social_spike", "one_sided_social") for f in manip_flags):
        return 15.0, "Abnormal, one-sided social activity — treated as a negative quality signal."
    ps = social.positive_share
    if ps is None:
        return 50.0, "Social mentions available but tone unknown."
    return 40 + 30 * min(1, max(0, (ps - 0.4) / 0.4)), f"{ps * 100:.0f}% positive mentions ({social.source})."


def composite(comp: dict[str, float], s: ScanSettings) -> float:
    w = s.weights.normalised()
    return sum(comp[k] * w[k] for k in w)


def evaluate(a: SetupAnalysis, comp: dict[str, float], *, s: ScanSettings, regime: str, manipulation: dict,
             liquidity_reasons: list[str], event_info: dict, event_decline: dict | None,
             earnings_known: bool) -> dict:
    """Apply penalties and hard exclusions on top of the weighted score."""
    base = composite(comp, s)
    penalties, exclusions = [], []
    if liquidity_reasons:
        exclusions += liquidity_reasons
    lvl = manipulation.get("level")
    if lvl == "high" and s.exclude_high_manipulation_risk:
        exclusions.append(f"Manipulation-risk score {manipulation['score']} (high) — excluded by default")
    elif lvl == "elevated":
        penalties.append(("Elevated abnormal-activity risk", -min(15, manipulation["score"] - 20)))
    if event_info.get("blocking"):
        exclusions += event_info["blocking"]
    if event_decline:
        exclusions.append(event_decline["message"])
    if not earnings_known:
        penalties.append(("Earnings date unknown", -5))
    if a.plan is None and a.status not in ("Avoid", "Invalidated"):
        exclusions.append("No valid trade plan")
    rr = a.plan.reward_risk if a.plan else None
    if a.plan and rr is not None and rr < s.min_reward_risk:
        if s.reward_risk_rule == "exclude":
            exclusions.append(f"Reward/risk {rr:.2f}:1 below the {s.min_reward_risk:.1f}:1 minimum")
        else:
            penalties.append((f"Reward/risk {rr:.2f}:1 below {s.min_reward_risk:.1f}:1", -10))
    if any("chasing" in w or "do not chase" in w for w in a.warnings):
        penalties.append(("Extended past the trigger", -5))
    score = max(0.0, min(100.0, base + sum(p for _, p in penalties)))
    status = a.status
    if exclusions and status not in ("Invalidated",):
        status = "Avoid"
    threshold = s.min_setup_score + (s.bearish_regime_score_add if regime == "bearish" else 0)
    qualifies = (status in ("Watch", "Approaching Entry", "Triggered") and score >= threshold
                 and comp["technical"] >= 50)
    gate = None
    if status in ("Watch", "Approaching Entry", "Triggered") and not qualifies:
        gate = (f"Technical quality {comp['technical']:.0f} below 50 — news or sentiment cannot override a weak "
                f"chart" if comp["technical"] < 50 else f"Score {score:.1f} below the {threshold:.0f} threshold"
                + (" (raised for a bearish market)" if regime == "bearish" else ""))
    return {"score": round(score, 1), "base_score": round(base, 1), "components": comp,
            "weights": s.weights.model_dump(), "penalties": [{"label": l_, "points": p} for l_, p in penalties],
            "exclusions": exclusions, "status": status, "qualifies": qualifies, "threshold": threshold,
            "gate_reason": gate, "is_probability": False}


def explain(a: SetupAnalysis, ev: dict, *, news_notes: list[str], event_info: dict, manipulation: dict,
            regime: str, sector_note: str, social_note: str) -> dict:
    passed = [s for s in a.signals if s.passed and s.group != "breakdown"]
    failed = [s for s in a.signals if not s.passed and s.group != "breakdown"]
    p = a.plan
    why = [f"{s.label}: {s.detail}" for s in sorted(passed, key=lambda s: -s.weight)[:6]]
    entry = []
    if p:
        entry.append(f"Entry zone {p.entry_zone_low:,.2f}–{p.entry_zone_high:,.2f} around the "
                     f"{a.metrics.get('support_label', 'support')}.")
        conf = next((s for s in a.signals if s.key == "confluence" and s.passed), None)
        if conf:
            entry.append(f"Confluence: {conf.detail}.")
        if p.reward_risk is not None:
            entry.append(f"Reward/risk to target 1 is {p.reward_risk:.2f}:1 "
                         f"({(p.target1 / p.entry_price - 1) * 100:+.1f}% vs {(p.stop / p.entry_price - 1) * 100:+.1f}%).")
    fail = []
    if p:
        fail.append(f"Invalidation: {p.stop_text}.")
    fail += [f"Weakness — {s.label.lower()}: {s.detail}" for s in sorted(failed, key=lambda s: -s.weight)[:4]]
    if regime == "bearish":
        fail.append("Bearish market regime: pullbacks fail more often.")
    fail.append(sector_note[0].upper() + sector_note[1:] + "." if sector_note else "")
    risks = list(event_info.get("blocking", [])) + [
        f"{u['kind'].replace('_', ' ').title()} {u['date']} ({u['trading_days_away']} trading days): {u['description']}"
        for u in event_info.get("upcoming", [])[:4]] + event_info.get("notes", [])
    risks += [f"{f['label']}: {f['value']}" for f in manipulation.get("flags", [])[:3]]
    risks += news_notes[:3]
    if social_note:
        risks.append(social_note)
    return {
        "why_qualifies": why or ["No positive signals."],
        "entry_attractive": entry or ["No valid entry plan."],
        "confirmation": [p.trigger_text] if p else [],
        "failure_risks": [x for x in fail if x],
        "news_event_risks": risks or ["No scheduled events or material news found in the lookback window."],
        "ranking_note": None,
    }


def ranking_notes(rows: list[dict]):
    """Explain why each ranked candidate sits above/below its neighbours (largest component differences)."""
    for i, r in enumerate(rows):
        parts = []
        for j, rel in ((i - 1, "below"), (i + 1, "above")):
            if 0 <= j < len(rows):
                o = rows[j]
                diffs = {k: r["score"]["components"][k] - o["score"]["components"][k] for k in COMPONENT_LABELS}
                k = max(diffs, key=lambda x: abs(diffs[x]))
                parts.append(f"Ranks {rel} {o['symbol']} ({r['score']['score']:.1f} vs {o['score']['score']:.1f}); "
                             f"largest difference: {COMPONENT_LABELS[k].lower()} ({diffs[k]:+.0f}).")
        r["explanation"]["ranking_note"] = " ".join(parts) or "Only qualifying candidate."
