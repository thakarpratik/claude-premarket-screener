"""Stock universe rules: U.S.-listed common stock on major exchanges, market-cap categories, liquidity filters."""
from __future__ import annotations

from .adapters.base import TickerInfo
from .config import ScanSettings

MAJOR_EXCHANGES = {"XNYS": "NYSE", "XNAS": "Nasdaq", "XASE": "NYSE American", "ARCX": "NYSE Arca", "BATS": "Cboe BZX"}
COMMON_TYPES = {"CS"}  # Polygon type code for common stock. ADRs (ADRC), ETFs, PFD, WARRANT, RIGHT, UNIT excluded.


def cap_category(market_cap: float | None, s: ScanSettings) -> str | None:
    if market_cap is None:
        return None
    if market_cap >= s.large_cap_min:
        return "large"
    if market_cap >= s.mid_cap_min:
        return "mid"
    if market_cap >= s.small_cap_min:
        return "small"
    return "micro"


def security_eligibility(info: TickerInfo, s: ScanSettings) -> list[str]:
    """Reasons a security is outside the universe (empty list = eligible on reference data)."""
    reasons = []
    if info.security_type not in COMMON_TYPES:
        reasons.append(f"Not common stock (type {info.security_type or 'unknown'})")
    if info.exchange not in MAJOR_EXCHANGES:
        reasons.append(f"Not listed on a major U.S. exchange ({info.exchange})")
    if not info.active:
        reasons.append("Inactive listing")
    cat = cap_category(info.market_cap, s)
    if cat is None:
        reasons.append("Market capitalization unavailable")
    elif cat == "micro":
        reasons.append(f"Micro-cap below ${s.small_cap_min / 1e6:,.0f}M minimum")
    elif cat not in s.cap_categories:
        reasons.append(f"{cat.title()}-cap excluded by settings")
    if s.sectors and info.sector not in s.sectors:
        reasons.append(f"Sector {info.sector or 'unknown'} excluded by settings")
    return reasons


def liquidity_eligibility(price: float | None, dollar_vol20: float | None, style: str, s: ScanSettings,
                          spread_pct: float | None = None, atr_pct: float | None = None) -> list[str]:
    reasons = []
    if price is None:
        return ["No current price"]
    if price < s.min_price:
        reasons.append(f"Price ${price:.2f} below ${s.min_price:.2f} minimum")
    if s.max_price is not None and price > s.max_price:
        reasons.append(f"Price ${price:.2f} above ${s.max_price:.2f} maximum")
    need = s.min_dollar_volume_intraday if style == "intraday" else s.min_dollar_volume_swing
    if dollar_vol20 is None:
        reasons.append("Average dollar volume unavailable")
    elif dollar_vol20 < need:
        reasons.append(f"Avg dollar volume ${dollar_vol20 / 1e6:.1f}M below ${need / 1e6:.0f}M {style} minimum")
    if spread_pct is not None and spread_pct > s.max_spread_pct:
        reasons.append(f"Bid-ask spread {spread_pct:.2f}% exceeds {s.max_spread_pct:.2f}%")
    if s.max_atr_pct is not None and atr_pct is not None and atr_pct > s.max_atr_pct:
        reasons.append(f"Volatility (ATR {atr_pct:.1f}%) above {s.max_atr_pct:.1f}% ceiling")
    return reasons
