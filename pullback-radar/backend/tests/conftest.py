import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATA_MODE", "demo")
os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ["PULLBACK_RADAR_NO_DOTENV"] = "1"

from pullback_radar import indicators as ind  # noqa: E402
from pullback_radar.adapters.demo import DemoMarket  # noqa: E402
from pullback_radar.market_calendar import ET  # noqa: E402

# Fixed clocks so the synthetic market is deterministic.
AFTER_CLOSE = datetime(2026, 10, 8, 18, 0, tzinfo=ET)
MIDDAY = datetime(2026, 10, 8, 11, 15, tzinfo=ET)


@pytest.fixture
def demo():
    return DemoMarket(now=AFTER_CLOSE)


@pytest.fixture
def demo_midday():
    return DemoMarket(now=MIDDAY)


def daily(m, sym):
    return ind.add_daily_indicators(m.daily_bars(sym, date(2025, 1, 1), date(2026, 10, 9)))
