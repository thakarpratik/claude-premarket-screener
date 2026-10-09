from pullback_radar.adapters.base import TickerInfo
from pullback_radar.config import ScanSettings
from pullback_radar.universe import cap_category, liquidity_eligibility, security_eligibility

S = ScanSettings()


def info(**kw):
    base = dict(symbol="X", name="X", exchange="XNAS", security_type="CS", market_cap=5e9, shares_float=None,
                sector="Technology", industry=None, source="t")
    base.update(kw)
    return TickerInfo(**base)


def test_cap_boundaries():
    assert cap_category(299e6, S) == "micro"
    assert cap_category(300e6, S) == "small"
    assert cap_category(2e9, S) == "mid"
    assert cap_category(10e9, S) == "large"
    assert cap_category(None, S) is None


def test_exclusions():
    assert security_eligibility(info(), S) == []
    assert any("Not common" in r for r in security_eligibility(info(security_type="ETF"), S))
    assert any("major" in r for r in security_eligibility(info(exchange="OTC"), S))
    assert any("Micro-cap" in r for r in security_eligibility(info(market_cap=100e6), S))
    assert any("unavailable" in r for r in security_eligibility(info(market_cap=None), S))
    s2 = ScanSettings(cap_categories=["large"])
    assert any("excluded by settings" in r for r in security_eligibility(info(), s2))


def test_liquidity_by_style():
    assert liquidity_eligibility(50, 10e6, "swing", S) == []
    assert liquidity_eligibility(50, 10e6, "intraday", S)  # needs $20M
    assert liquidity_eligibility(3, 50e6, "swing", S)  # below min price
    assert liquidity_eligibility(50, 50e6, "swing", S, spread_pct=1.0)  # spread too wide
    assert liquidity_eligibility(None, 50e6, "swing", S) == ["No current price"]
