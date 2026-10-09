# API reference

Base path `/api`. Interactive OpenAPI docs: `http://localhost:8000/docs` (schema at `/openapi.json`).

**Authentication.** `POST /api/auth/login` or `/register` sets an HttpOnly `pr_session` cookie and also
returns `token`, which non-browser clients can send as `Authorization: Bearer <token>`. Every endpoint
except `health`, `status` and `auth/*` needs a session.

**CSRF.** Cookie-authenticated `POST`/`PUT`/`PATCH`/`DELETE` requests must send
`X-Requested-With: pullback-radar`. Without it the server returns 403.

**Data honesty.** Responses carry `synthetic` (demo data), `generated_at`, `session` and per-card
`price_time` plus `freshness` `{status: real-time|delayed|end-of-day|stale|unavailable|synthetic, note}`.
When providers are not configured, scan endpoints return
`{"ok": false, "setup_required": true, "messages": [...]}`.

## Meta
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness. |
| GET | `/status` | Data mode, configured providers, plan delay, setup messages, request/error counters, market session. |

## Auth
| Method | Path | Body |
|---|---|---|
| POST | `/auth/register` | `{email, password}` (password ≥ 10 chars). Disabled when `ALLOW_REGISTRATION=false`. |
| POST | `/auth/login` | `{email, password}`. 8 failed attempts per 15 min per email+IP → 429. |
| POST | `/auth/logout` | — |
| GET | `/auth/me` | — |

## Settings
| Method | Path | Description |
|---|---|---|
| GET | `/settings` | `{settings, defaults}` |
| PUT | `/settings` | Full `ScanSettings` object (see `config.py`). Validation failures → 422 with field messages. Changing settings invalidates the cached scan. |

## Scans and opportunities
| Method | Path | Description |
|---|---|---|
| POST | `/scan` | Run a scan now with your settings. Also evaluates alerts and updates open paper trades. |
| GET | `/scan/latest` | Latest scan (re-used for 2 min while the market is open, 15 min otherwise): `market`, `intraday`, `swing` (`ranked`, `watch_only`, `avoid`, `flagged_count`, `message`), `errors`, `excluded`. |
| GET | `/market` | Market overview only: indices, sectors, volatility, breadth, regime + guidance, economic events, market news. |
| GET | `/opportunities/{intraday\|swing}` | Ranked cards plus developing and avoid lists. `message` is `"No qualifying setups right now."` when nothing ranks. |
| GET | `/flagged` | Stocks with elevated or high manipulation risk, for review only. |
| GET | `/excluded` | Symbols outside the universe, with reasons, plus data errors. |
| GET | `/news` | Company news across scanned stocks (with verification tier, sentiment, impact, why it matters, priced-in) and market news. |
| GET | `/stocks/{symbol}` | Research view: `info`, `cards.{swing,intraday}`, `chart.daily` (OHLCV, SMA20/50/200, RSI, MACD), `chart.intraday` (5-min bars + VWAP; times encoded as ET wall clock), `universe_reasons`. |

### Card fields (abridged)
`symbol, name, exchange, sector, market_cap, cap_category, style, status (Watch | Approaching Entry |
Triggered | Invalidated | Avoid), setup_type, technical_state, qualifies, rank, price, price_time,
freshness, plan {entry_zone_low, entry_zone_high, trigger_price, trigger_text, entry_price, stop, stop_text,
target1, target1_text, target2, target2_text, conditional_text, risk_per_share, reward_risk, reward_risk_t2,
gain_pct_t1, gain_pct_t2, loss_pct}, levels[], signals[{key,label,passed,detail,group,weight}], metrics{},
score {score, base_score, components, weights, penalties, exclusions, threshold, gate_reason,
is_probability:false}, explanation {why_qualifies, entry_attractive, confirmation, failure_risks,
news_event_risks, ranking_note}, manipulation {score, level, label, flags[], unknown[]}, news[], events
{upcoming, blocking, notes, next_earnings}, event_driven_decline, position_size, warnings[], reasons_avoid[],
social, sources`.

## Watchlist and alerts
| Method | Path | Description |
|---|---|---|
| GET | `/watchlist` | Items with the plan saved when added (`plan_snapshot`) and the current card summary. |
| POST | `/watchlist` | `{symbol, style, note?}` |
| DELETE | `/watchlist/{id}` | — |
| GET | `/alerts?unread_only=` | Newest first: `title, message, price, price_time, link, synthetic, read`. Each message ends with "Alert only — no order has been placed." |
| POST | `/alerts/{id}/read`, `/alerts/read-all` | — |
| GET | `/alert-rules` | Alert kinds and your rules. Default rules (one per kind) apply to every watchlist item. |
| POST | `/alert-rules` | `{kind, symbol?, params, enabled}`. Custom level: `{kind:"price_cross", symbol, params:{above:X}}` or `{below:X}`. |
| PATCH/DELETE | `/alert-rules/{id}` | Toggle `enabled` or change `params` (`unusual_activity.rel_volume`, `event.event_days`). |

Alert kinds: `entry_zone, trigger, invalidation, target1, target2, news, unusual_activity, manipulation,
event, price_cross`. An alert fires when its condition turns true, and fires again only after the
condition has been false. A news alert also fires for each new material item.

## Risk and journal
| Method | Path | Description |
|---|---|---|
| POST | `/risk/position-size` | `{equity, risk_pct, entry, stop, slippage_per_share, commission_per_trade, max_position_pct, targets[], min_reward_risk}` → shares, position value, total planned risk (incl. slippage on entry and stop fills and both commissions), profit and R:R per target, warnings. |
| GET | `/risk/daily-loss` | Realized P&L today vs. your daily loss limit, separately for actual and paper trades. |
| GET | `/trades` | Trades plus statistics (win rate, average win/loss, expectancy, profit factor, max drawdown, average R, by strategy). Paper and actual are reported separately. |
| POST | `/trades` | Record a paper or actual trade. A stop at or above the entry → 422. |
| PATCH | `/trades/{id}` | Update exit, notes, stop, targets and similar fields. Setting `exit_price` closes the trade. |
| DELETE | `/trades/{id}` | — |
| POST/GET | `/trades/{id}/screenshot` | Upload (PNG/JPEG/WebP, max 5 MB) or fetch a chart screenshot. |
| POST | `/paper/from-setup` | `{symbol, style, quantity?}` records a paper entry at the latest observed quote with the setup's stop and targets. Refused (409) unless the setup is **Triggered**. No order is sent anywhere. |

## Backtests
| Method | Path | Description |
|---|---|---|
| POST | `/backtests` | `{style, symbols[], start?, end?, days_intraday, min_reward_risk, max_hold_bars, entry_window_bars, slippage_per_share, commission_per_order, in_sample_pct, require_market_uptrend}`. Runs in the background and returns `{id, status:"running"}`. |
| GET | `/backtests` | Recent runs with summary metrics. |
| GET | `/backtests/{id}` | `result`: `overall`, `by_period` (in/out-of-sample), `by_regime`, `by_setup`, `trades[]`, `safeguards[]`, `warnings[]` (synthetic data, small sample). |
