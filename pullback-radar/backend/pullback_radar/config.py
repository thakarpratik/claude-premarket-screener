"""Server configuration (environment) and the per-user scan settings model.

Environment settings hold secrets and deployment choices and never leave the server.
`ScanSettings` holds the user-tunable screening rules; every default below comes from the
product specification and can be changed per user in the Settings page."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = BASE_DIR / ".env"):
    """Minimal .env loader (KEY=VALUE lines). Real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if not os.environ.get("PULLBACK_RADAR_NO_DOTENV"):
    _load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


class EnvSettings(BaseModel):
    """Read once at start-up. Secrets stay here and are never serialised to the client."""
    data_mode: Literal["demo", "live"] = "demo"
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'pullback_radar.db'}"
    # Price source in live mode: "auto" = Polygon when POLYGON_API_KEY is set, otherwise Yahoo Finance.
    price_provider: Literal["auto", "polygon", "yahoo"] = "auto"
    yahoo_delay_minutes: int = 15
    yahoo_requests_per_minute: int = 300
    polygon_api_key: str | None = None
    polygon_base_url: str = "https://api.polygon.io"
    # Polygon's stocks plans differ in latency. State yours honestly: 0 = real-time, 15 = 15-minute delayed.
    polygon_delay_minutes: int = 15
    polygon_requests_per_minute: int = 5
    finnhub_api_key: str | None = None
    finnhub_requests_per_minute: int = 30
    sec_user_agent: str | None = None  # SEC EDGAR requires "Company Name contact@email" in the User-Agent
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    universe_symbols: list[str] = Field(default_factory=list)  # optional explicit universe (live mode)
    max_candidates: int = 250  # live mode: deep-scan at most this many liquid names per run
    cache_dir: Path = BASE_DIR / "data" / "cache"
    upload_dir: Path = BASE_DIR / "data" / "uploads"
    scheduler_enabled: bool = True
    allow_registration: bool = True
    cookie_secure: bool = False
    session_days: int = 14
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @classmethod
    def from_env(cls) -> "EnvSettings":
        syms = [s.strip().upper() for s in (_env("UNIVERSE_SYMBOLS", "") or "").split(",") if s.strip()]
        kw = dict(
            data_mode=(_env("DATA_MODE", "demo") or "demo").lower(),
            price_provider=(_env("PRICE_PROVIDER", "auto") or "auto").lower(),
            yahoo_delay_minutes=int(_env("YAHOO_DELAY_MINUTES", "15")),
            yahoo_requests_per_minute=int(_env("YAHOO_REQUESTS_PER_MINUTE", "300")),
            polygon_api_key=_env("POLYGON_API_KEY"),
            polygon_base_url=_env("POLYGON_BASE_URL", "https://api.polygon.io"),
            polygon_delay_minutes=int(_env("POLYGON_DELAY_MINUTES", "15")),
            polygon_requests_per_minute=int(_env("POLYGON_REQUESTS_PER_MINUTE", "5")),
            finnhub_api_key=_env("FINNHUB_API_KEY"),
            finnhub_requests_per_minute=int(_env("FINNHUB_REQUESTS_PER_MINUTE", "30")),
            sec_user_agent=_env("SEC_USER_AGENT"),
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-opus-5-5"),
            universe_symbols=syms,
            max_candidates=int(_env("MAX_CANDIDATES", "250")),
            scheduler_enabled=_env_bool("SCHEDULER_ENABLED", True),
            allow_registration=_env_bool("ALLOW_REGISTRATION", True),
            cookie_secure=_env_bool("COOKIE_SECURE", False),
            session_days=int(_env("SESSION_DAYS", "14")),
            cors_origins=[o.strip() for o in (_env("CORS_ORIGINS", "http://localhost:3000") or "").split(",") if o.strip()],
        )
        if _env("DATABASE_URL"):
            kw["database_url"] = _env("DATABASE_URL")
        if _env("CACHE_DIR"):
            kw["cache_dir"] = Path(_env("CACHE_DIR"))
        if _env("UPLOAD_DIR"):
            kw["upload_dir"] = Path(_env("UPLOAD_DIR"))
        return cls(**kw)


class ScoreWeights(BaseModel):
    """Starting weights from the specification. Not assumed optimal: validate with the backtester."""
    technical: float = 25
    trend_rs: float = 20
    entry: float = 15
    volume_liquidity: float = 15
    news: float = 10
    market_alignment: float = 10
    sentiment: float = 5

    @model_validator(mode="after")
    def _positive(self):
        vals = self.model_dump().values()
        if any(v < 0 for v in vals) or sum(vals) <= 0:
            raise ValueError("weights must be non-negative and not all zero")
        return self

    def normalised(self) -> dict[str, float]:
        d = self.model_dump()
        total = sum(d.values())
        return {k: v / total for k, v in d.items()}


class ScanSettings(BaseModel):
    # Market-cap categories (USD)
    small_cap_min: float = 300e6
    mid_cap_min: float = 2e9
    large_cap_min: float = 10e9
    cap_categories: list[Literal["small", "mid", "large"]] = Field(default_factory=lambda: ["small", "mid", "large"])
    # Price and liquidity filters
    min_price: float = 5.0
    max_price: float | None = None
    min_dollar_volume_intraday: float = 20e6
    min_dollar_volume_swing: float = 5e6
    max_spread_pct: float = 0.5  # applied only when a reliable quote is available
    max_atr_pct: float | None = None  # optional volatility ceiling (ATR as % of price)
    sectors: list[str] = Field(default_factory=list)  # empty = all sectors
    # Strategy
    trading_styles: list[Literal["intraday", "swing"]] = Field(default_factory=lambda: ["intraday", "swing"])
    swing_hold_days_min: int = 2
    swing_hold_days_max: int = 10
    opening_range_minutes: Literal[5, 15, 30] = 15
    min_setup_score: float = 60
    bearish_regime_score_add: float = 15  # extra score a long setup needs in a bearish market
    # Risk
    min_reward_risk: float = 2.0
    reward_risk_rule: Literal["warn", "exclude"] = "warn"
    account_equity: float = 25_000
    risk_pct_per_trade: float = 0.5
    max_position_pct: float = 25.0
    slippage_per_share: float = 0.02
    commission_per_trade: float = 0.0
    daily_loss_limit_pct: float = 2.0
    # Event risk
    earnings_blackout_days_swing: int = 5  # trading days before earnings in which swing setups are excluded
    earnings_blackout_days_intraday: int = 1
    exclude_before_earnings: bool = True
    exclude_before_major_macro: bool = False
    # Manipulation
    exclude_high_manipulation_risk: bool = True
    # Ranking
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    # Notifications
    notify_in_app: bool = True
    notify_email: bool = False

    @field_validator("risk_pct_per_trade")
    @classmethod
    def _risk_bounds(cls, v):
        if not 0 < v <= 5:
            raise ValueError("risk per trade must be between 0 and 5% of equity")
        return v

    @model_validator(mode="after")
    def _caps(self):
        if not (0 < self.small_cap_min < self.mid_cap_min < self.large_cap_min):
            raise ValueError("market-cap thresholds must increase: small < mid < large")
        if self.max_price is not None and self.max_price <= self.min_price:
            raise ValueError("max price must exceed min price")
        if self.swing_hold_days_min > self.swing_hold_days_max:
            raise ValueError("swing holding period min must not exceed max")
        return self


ENV = EnvSettings.from_env()
