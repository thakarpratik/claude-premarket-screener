"""Sector naming and sector-ETF benchmarks (SPDR Select Sector funds are used as sector proxies)."""

SECTOR_ETFS = {
    "Technology": "XLK", "Financials": "XLF", "Health Care": "XLV", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Energy": "XLE", "Industrials": "XLI", "Materials": "XLB",
    "Utilities": "XLU", "Real Estate": "XLRE", "Communication Services": "XLC",
}
INDEX_PROXIES = {
    "SPY": "S&P 500 (SPY proxy)", "QQQ": "Nasdaq-100 (QQQ proxy for Nasdaq Composite)",
    "DIA": "Dow Jones (DIA proxy)", "IWM": "Russell 2000 (IWM proxy)",
}


def sector_from_sic(sic: str | int | None) -> str | None:
    """Map a 4-digit SEC SIC code to a broad GICS-style sector. Approximate by design."""
    try:
        c = int(sic)
    except (TypeError, ValueError):
        return None
    if c in (2834, 2835, 2836) or 3841 <= c <= 3851 or 8000 <= c <= 8099 or c == 5047 or c == 5122:
        return "Health Care"
    if 3570 <= c <= 3579 or 3670 <= c <= 3679 or 7370 <= c <= 7379 or c in (3661, 3663, 3669, 3825, 3826):
        return "Technology"
    if 4800 <= c <= 4899 or 2710 <= c <= 2741 or 7810 <= c <= 7841:
        return "Communication Services"
    if 6000 <= c <= 6499 or 6700 <= c <= 6799:
        return "Financials"
    if 6500 <= c <= 6599 or c == 6798:
        return "Real Estate"
    if 4900 <= c <= 4949 or c == 4991:
        return "Utilities"
    if 1300 <= c <= 1399 or 2900 <= c <= 2999 or c in (4922, 4923, 5171, 5172):
        return "Energy"
    if 1000 <= c <= 1499 or 2600 <= c <= 2699 or 2800 <= c <= 2899 or 3300 <= c <= 3399 or 3200 <= c <= 3299:
        return "Materials"
    if 2000 <= c <= 2199 or 5400 <= c <= 5499 or c in (2840, 2844, 5140, 5141, 5149, 5180, 5181, 5182):
        return "Consumer Staples"
    if (2200 <= c <= 2399 or 2500 <= c <= 2599 or 3711 <= c <= 3716 or 5000 <= c <= 5999 or 7000 <= c <= 7099
            or 7900 <= c <= 7999 or c in (3942, 3944, 3949, 8200)):
        return "Consumer Discretionary"
    if 1500 <= c <= 1799 or 3400 <= c <= 3569 or 3580 <= c <= 3669 or 3700 <= c <= 3799 or 4000 <= c <= 4799 \
            or 7380 <= c <= 7389 or 8700 <= c <= 8748 or 3800 <= c <= 3840:
        return "Industrials"
    return None


def sector_etf(sector: str | None) -> str | None:
    return SECTOR_ETFS.get(sector or "")
