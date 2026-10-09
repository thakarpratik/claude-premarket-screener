import pytest

from pullback_radar.config import ScanSettings, ScoreWeights
from pullback_radar.scanner.common import SetupAnalysis, Signal, TradePlan
from pullback_radar.services import risk, scoring


def plan(entry=100, stop=95, t1=115, t2=120):
    return TradePlan(98, 101, entry, "t", entry, stop, "s", t1, "t1", t2, "t2", "c")


def setup(status="Approaching Entry", tech_pass=True, rr_plan=None):
    a = SetupAnalysis("X", "swing", True, status, "healthy_pullback", "Pullback", None, 100.0)
    for i in range(4):
        a.signals.append(Signal(f"p{i}", "p", tech_pass, "", "pullback"))
        a.signals.append(Signal(f"t{i}", "t", True, "", "trend"))
    a.signals.append(Signal("v", "v", True, "", "volume"))
    a.plan = rr_plan or plan()
    a.metrics = {"atr": 2.0, "dollar_volume_20d": 100e6}
    return a


def ev(a, s=ScanSettings(), regime="bullish", manip=None, news_score=50, liq=None, blocking=None):
    comp = scoring.components(a, news_score=news_score, regime=regime, sector_ok=True, social_score=None, spread_pct=None)
    return scoring.evaluate(a, comp, s=s, regime=regime, manipulation=manip or {"level": "low", "score": 5},
                            liquidity_reasons=liq or [], event_info={"blocking": blocking or []}, event_decline=None,
                            earnings_known=True)


def test_good_setup_qualifies_and_is_not_a_probability():
    r = ev(setup())
    assert r["qualifies"] and r["is_probability"] is False


def test_news_cannot_override_broken_technicals():
    r = ev(setup(tech_pass=False), news_score=100)
    assert not r["qualifies"] and "cannot override" in r["gate_reason"]


def test_high_manipulation_excluded():
    r = ev(setup(), manip={"level": "high", "score": 80})
    assert r["status"] == "Avoid" and not r["qualifies"]


def test_liquidity_and_blackout_are_hard_exclusions():
    assert ev(setup(), liq=["Avg dollar volume too low"])["status"] == "Avoid"
    assert ev(setup(), blocking=["Earnings in 2 days"])["status"] == "Avoid"


def test_reward_risk_rule_warn_vs_exclude():
    low = setup(rr_plan=plan(t1=104, t2=None))  # 0.8 R
    warn = ev(low)
    assert any("Reward/risk" in p["label"] for p in warn["penalties"]) and warn["status"] != "Avoid"
    excl = ev(low, s=ScanSettings(reward_risk_rule="exclude"))
    assert excl["status"] == "Avoid"


def test_bearish_regime_raises_threshold():
    s = ScanSettings()
    assert ev(setup(), regime="bearish")["threshold"] == s.min_setup_score + s.bearish_regime_score_add


def test_weights_validation_and_normalisation():
    w = ScoreWeights(technical=50, trend_rs=50, entry=0, volume_liquidity=0, news=0, market_alignment=0, sentiment=0)
    assert w.normalised()["technical"] == 0.5
    with pytest.raises(ValueError):
        ScoreWeights(technical=-1)


def test_settings_validation():
    with pytest.raises(ValueError):
        ScanSettings(small_cap_min=5e9)
    with pytest.raises(ValueError):
        ScanSettings(risk_pct_per_trade=10)


def test_plan_validity():
    assert plan(stop=101).validity_errors()
    assert plan(t1=99).validity_errors()
    p = plan()
    assert p.reward_risk == pytest.approx(3.0)
    d = p.to_dict()
    assert d["gain_pct_t1"] == 15.0 and d["loss_pct"] == -5.0


def test_position_size_math():
    r = risk.position_size(equity=25000, risk_pct=0.5, entry=50, stop=48, slippage_per_share=0.05,
                           commission_per_trade=1, max_position_pct=100, targets=[56])
    # budget 125, minus 2 commission = 123; risk/share 2.10 -> 58 shares
    assert r["shares"] == 58
    assert r["total_planned_risk"] == pytest.approx(58 * 2.10 + 2, abs=0.01)
    assert r["targets"][0]["reward_risk"] == 3.0
    assert r["targets"][0]["net_profit"] == pytest.approx(58 * (6 - 0.10) - 2, abs=0.01)


def test_position_size_cap_and_warnings():
    r = risk.position_size(equity=10000, risk_pct=1, entry=100, stop=99.9, max_position_pct=10, targets=[100.1])
    assert r["limited_by"] == "max position size" and r["shares"] == 10
    assert any("below your" in w for w in r["warnings"])
    assert not risk.position_size(equity=1000, risk_pct=1, entry=10, stop=11)["valid"]


def test_daily_loss_limit():
    s = risk.daily_loss_status(-600, 25000, 2)
    assert s["reached"] and "Do not increase size" in s["message"]
    assert not risk.daily_loss_status(-100, 25000, 2)["reached"]
