"""Shared setup/plan structures and plan validation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

STATUSES = ("Watch", "Approaching Entry", "Triggered", "Invalidated", "Avoid")


@dataclass
class Signal:
    key: str
    label: str
    passed: bool
    detail: str
    group: str  # trend | pullback | confirmation | volume | relative_strength | breakdown | structure
    weight: float = 1.0

    def to_dict(self):
        return asdict(self)


@dataclass
class Level:
    price: float
    label: str
    kind: str  # support | resistance | ma | vwap | pivot | breakout | opening_range | target | stop | trigger

    def to_dict(self):
        return {"price": round(self.price, 2), "label": self.label, "kind": self.kind}


@dataclass
class TradePlan:
    entry_zone_low: float
    entry_zone_high: float
    trigger_price: float
    trigger_text: str
    entry_price: float  # reference entry used for R:R (the trigger level, never a chased price)
    stop: float
    stop_text: str
    target1: float
    target1_text: str
    target2: float | None
    target2_text: str | None
    conditional_text: str

    @property
    def risk_per_share(self) -> float:
        return self.entry_price - self.stop

    @property
    def reward_risk(self) -> float | None:
        r = self.risk_per_share
        return (self.target1 - self.entry_price) / r if r > 0 else None

    @property
    def reward_risk_t2(self) -> float | None:
        r = self.risk_per_share
        return (self.target2 - self.entry_price) / r if r > 0 and self.target2 else None

    def validity_errors(self) -> list[str]:
        errs = []
        vals = [self.entry_price, self.stop, self.target1]
        if any(v is None or v != v or v <= 0 for v in vals):
            return ["Plan has missing or non-positive prices"]
        if self.stop >= self.entry_price:
            errs.append("Stop is not below the entry")
        if self.target1 <= self.entry_price:
            errs.append("First target is not above the entry")
        if self.target2 is not None and self.target2 < self.target1:
            errs.append("Second target is below the first")
        if self.entry_zone_low > self.entry_zone_high:
            errs.append("Entry zone is inverted")
        return errs

    def to_dict(self):
        d = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}
        rr, rr2 = self.reward_risk, self.reward_risk_t2
        d["risk_per_share"] = round(self.risk_per_share, 2)
        d["reward_risk"] = round(rr, 2) if rr is not None else None
        d["reward_risk_t2"] = round(rr2, 2) if rr2 is not None else None
        d["gain_pct_t1"] = round((self.target1 / self.entry_price - 1) * 100, 2)
        d["gain_pct_t2"] = round((self.target2 / self.entry_price - 1) * 100, 2) if self.target2 else None
        d["loss_pct"] = round((self.stop / self.entry_price - 1) * 100, 2)
        return d


@dataclass
class SetupAnalysis:
    symbol: str
    style: str  # swing | intraday
    is_setup: bool
    status: str
    technical_state: str  # healthy_pullback | stabilizing | breaking_down | extended | no_trend | insufficient_data ...
    setup_type: str | None
    as_of: str | None
    price: float | None
    signals: list[Signal] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    plan: TradePlan | None = None
    reasons_avoid: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def passed(self, group: str | None = None) -> list[Signal]:
        return [s for s in self.signals if s.passed and (group is None or s.group == group)]

    def to_dict(self):
        return {
            "symbol": self.symbol, "style": self.style, "is_setup": self.is_setup, "status": self.status,
            "technical_state": self.technical_state, "setup_type": self.setup_type, "as_of": self.as_of,
            "price": round(self.price, 2) if self.price is not None else None,
            "signals": [s.to_dict() for s in self.signals], "levels": [lv.to_dict() for lv in self.levels],
            "metrics": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.metrics.items()},
            "plan": self.plan.to_dict() if self.plan else None,
            "reasons_avoid": self.reasons_avoid, "warnings": self.warnings,
        }


def fmt(p: float) -> str:
    return f"${p:,.2f}"
