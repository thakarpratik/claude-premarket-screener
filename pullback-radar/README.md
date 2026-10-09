# Market Pullback Radar

An AI-assisted U.S. stock scanner for **intraday** and **swing** pullback setups. It looks for liquid
small-, mid- and large-cap stocks that have pulled back inside a healthy trend toward meaningful support,
and gives each one a conditional plan: entry zone, confirmation trigger, stop/invalidation level, two
targets, reward-to-risk and a position size. It filters out pump-and-dump patterns, event-driven drops,
illiquid names and earnings blackouts.

> Research and decision-support tool only. It is not financial advice, it promises no outcomes, and it
> **never places orders**. "No qualifying setups right now" is a normal, often correct, result.

## What's included

| Area | Where |
|---|---|
| Market regime (S&P 500 / Nasdaq / Dow / Russell 2000 proxies, sectors, volatility, breadth, events) | `backend/pullback_radar/services/market.py` |
| Universe and market-cap categories, liquidity filters | `backend/pullback_radar/universe.py` |
| Swing pullback scanner (daily + weekly) | `backend/pullback_radar/scanner/swing.py` |
| Intraday scanner (pre-market, opening range, VWAP, 1/5/15-min) | `backend/pullback_radar/scanner/intraday.py` |
| News classification, verification tiers, "priced in" check, optional Claude interpretation | `services/news.py`, `services/ai_news.py` |
| Pump-and-dump / abnormal-activity risk score (0–100) | `services/manipulation.py` |
| Earnings/event blackout, event-driven-decline detection | `services/events.py` |
| Transparent ranking, hard exclusions, explanations | `services/scoring.py` |
| Position sizing, daily loss limit | `services/risk.py` |
| Alerts with de-duplication | `services/alerts.py` |
| Paper trading and journal statistics | `services/journal.py` |
| Backtester (swing and intraday, costs, out-of-sample, regimes, no look-ahead) | `services/backtest.py` |
| Replaceable data adapters: Polygon, Finnhub, SEC EDGAR, labelled demo | `backend/pullback_radar/adapters/` |
| FastAPI app, auth, scheduler | `app.py`, `auth.py`, `scheduler.py`, `db.py` |
| Next.js dashboard (11 pages, interactive charts) | `web/` |

Docs: [API](docs/API.md) · [Methodology](docs/METHODOLOGY.md) · [Deployment](docs/DEPLOYMENT.md) ·
[Database schema](docs/schema.sql)

## Quick start (demo mode, no keys)

Requirements: Python 3.11+, Node 20+.

```bash
# Backend
cd pullback-radar/backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                                     # DATA_MODE=demo by default
uvicorn pullback_radar.app:create_app --factory --port 8000

# Frontend (second terminal)
cd pullback-radar/web
npm install
cp .env.example .env.local                               # BACKEND_URL=http://127.0.0.1:8000
npm run dev                                              # http://localhost:3000
```

Create an account on the login page and press **Scan now**.

Demo mode serves a deterministic **synthetic** market (tickers `DEMO01`…`DEMO18`, names ending in
"(synthetic)", news links to example.com). A banner on every page says so, every card shows freshness
`synthetic`, and backtest results carry a synthetic-data warning. The scenarios cover healthy pullbacks at
each status, a breakdown, a pump-and-dump, a spike-and-crash with an offering, an earnings gap-down, an
earnings blackout, an illiquid stock, a micro-cap, an ETF and an OTC listing.

Or run the whole stack with PostgreSQL: `cp backend/.env.example backend/.env && docker compose up --build`.

## Live mode

Set `DATA_MODE=live` and add keys to `backend/.env`:

| Variable | Provider | Used for | Needed? |
|---|---|---|---|
| `POLYGON_API_KEY` | [Polygon.io](https://polygon.io) | daily/intraday OHLCV, quotes, grouped daily (universe), reference data, market cap, splits, news with sentiment | **required** |
| `POLYGON_DELAY_MINUTES` | — | your plan's latency, so prices are labelled correctly | set to match the plan |
| `FINNHUB_API_KEY` | [Finnhub](https://finnhub.io) | earnings calendar, extra company news, social sentiment (premium), economic calendar (premium) | recommended |
| `SEC_USER_AGENT` | [SEC EDGAR](https://www.sec.gov/os/accessing-edgar-data) | dilution filings (S-1/S-3/424B), late filings | recommended (free) |
| `ANTHROPIC_API_KEY` | [Anthropic](https://platform.claude.com) | optional AI interpretation of headlines, shown as "AI interpretation" | optional |

If the Polygon key is missing, the API returns `setup_required` and the UI shows **"Scanner offline —
setup required"**. It never falls back to synthetic data. A missing optional source is reported in the
card (for example "Earnings calendar unavailable — verify the next report date"), and that card gets a
score penalty.

Polygon's free tier (5 requests/min, end-of-day data) is fine for trying things out. A scan of hundreds of
symbols, intraday data and real-time quotes need a paid stocks plan. `MAX_CANDIDATES` and
`UNIVERSE_SYMBOLS` control how much each scan requests.

## Testing

```bash
cd pullback-radar/backend && pytest -q          # 81 tests
cd pullback-radar/web && npm run typecheck && npm run build
```

The tests cover:

- Screening logic for every demo scenario.
- No look-ahead: past analysis is unchanged when future bars are added (swing and intraday).
- Data timestamps and freshness labels: real-time, delayed, stale, end-of-day, synthetic, unavailable.
- Market-cap boundaries, and the ETF/OTC/micro-cap exclusions.
- Plan validity and reward/risk, position-sizing maths, and the daily loss limit.
- Alert de-duplication and re-arming.
- Backtest fill rules: the stop is assumed to fill first when a bar hits both stop and target, gaps through the stop fill at the open, partial exit at T1 then breakeven, time exits, and fills only after the signal bar.
- Provider parsing with mocked HTTP: retries on 429, giving up after repeated failures, caching, rate limiting, and 401/403 surfacing as configuration errors.
- Live mode without keys never shows synthetic data.
- The API end to end: auth, CSRF header, feeds, flagged names kept out of the feed, paper trades refused before the trigger, settings validation, and journal CRUD with image upload.
- PostgreSQL: checked by hand against PostgreSQL 16 (see Deployment).

## Non-negotiable rules the code enforces

- **No fabricated numbers.** Every level is computed from timestamped bars. Prices show their time and
  freshness, and a closed market is labelled "levels may no longer be valid".
- **No buy just because price is near support.** Untriggered setups say so and show the exact trigger.
  Paper entries are refused until the trigger is met. A daily-close trigger cannot be confirmed from a
  forming bar.
- **Breakdowns are not dips.** Losing the 50-day, broken support on volume, a lower low, an over-deep
  retracement or an event-driven gap-down all mean *Avoid*.
- **News cannot rescue a weak chart.** Ranking needs a technical score of at least 50, whatever the news or sentiment.
- **Manipulation risk.** High-risk names are excluded by default and only appear on a separate Flagged page.
  The score describes patterns and never claims a stock was manipulated.
- **Invalid or incomplete risk/reward means no recommendation.** Below-minimum R:R gets a warning or is
  excluded, depending on Settings.
- **Scores are not probabilities.** The app shows a rules-based setup-quality score. No probability is
  displayed, because no calibrated model has been validated.
- **Simulated ≠ actual.** Paper and actual journal statistics are kept apart. Backtests are labelled as
  simulations and flagged for small samples, synthetic data and survivorship bias.

## Relationship to the rest of this repository

This app lives entirely in `pullback-radar/` and does not change the existing Move Radar app at the
repository root. The root `tsconfig.json` excludes this folder so the root Vercel build is unaffected.
