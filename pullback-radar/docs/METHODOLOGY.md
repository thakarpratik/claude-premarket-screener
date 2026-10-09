# Methodology

Every rule below is implemented in code you can read. Thresholds are starting points, not proven optimal:
validate them with the backtester before relying on them.

## 1. Market regime (`services/market.py`)
- **Benchmarks.** SPY, QQQ, DIA and IWM act as proxies for the S&P 500, Nasdaq (via the Nasdaq-100),
  Dow and Russell 2000 (index feeds need separate entitlements). The 11 SPDR sector ETFs act as sector
  benchmarks.
- **Points.** For SPY, QQQ and IWM: ±1 each for being above the 50-day, above the 200-day, and having a
  rising 50-day. Breadth across the scanned universe adds ±1 (≥60% / ≤40% of stocks above their 50-day).
  SPY 20-day realized volatility subtracts 1 at ≥20% and 2 at ≥30%. VIX is not in the feed, so it is not
  shown.
- **Label.** **Bullish** if the total is ≥ 5 and SPY is above its 50-day. **Bearish** if the total is ≤ −2,
  or SPY is below its 200-day with a falling 50-day. Otherwise **mixed**.
- **Effect on scanning.** In a bearish regime a long setup needs `min_setup_score + bearish_regime_score_add`
  (default 60 + 15) to rank.

## 2. Universe (`universe.py`)
- **Included.** Polygon type `CS` (common stock) on NYSE, Nasdaq, NYSE American, NYSE Arca or Cboe BZX.
- **Excluded.** ETFs, ADRs, preferreds, warrants, rights, units, OTC listings, and anything without a market cap.
- **Cap categories.** Small $300M–$2B, mid $2B–$10B, large ≥ $10B. All configurable. Anything below the
  small-cap minimum counts as micro-cap and is excluded.
- **Liquidity.** Average 20-day dollar volume of at least $20M for intraday or $5M for swing. Price ≥ $5.
  Spread ≤ 0.5% when a quote is available. Optional ATR% ceiling.
- **Live universe build.** Grouped daily bars for about 20 sessions keep the liquid common stocks. The
  `MAX_CANDIDATES` most liquid are then deep-scanned.

## 3. Swing pullback scanner (`scanner/swing.py`)
Evaluated on the latest daily bar. Swing pivots are used only once confirmed, 3 bars after the pivot.

| Group | Signals |
|---|---|
| Trend | Above a rising 50-day (10-day slope); 20-day rising; 50-day above 200-day; weekly close above a rising 10-week average |
| Pullback | Off the 20-day swing high by 2–15%, ≤ 61.8% retracement, 2–15 sessions old; RSI peaked ≥ 60 in the advance and is now 38–58 |
| Structure | Support within 1 ATR (20-day, 50-day, 10-day EMA, prior breakout pivot, last confirmed swing low, else the pullback low); confluence (≥ 2 levels within 0.5 ATR); higher low |
| Confirmation | MACD histogram rising; bullish reversal candle (hammer, engulfing, close above the prior high) |
| Volume | Pullback volume < 0.85× the advance; buying volume ≥ 1.2× average on an up bar; A/D line rising over 20 sessions |
| Relative strength | 3-month return vs SPY and vs the sector ETF |

**Breakdown, so the status is Avoid.** Two or more of the following, or a support broken on volume:
close more than 0.5 ATR below the 50-day; support broken by more than 0.5 ATR on ≥ 1.3× volume; pullback
deeper than 20% or 78.6%; an undercut of the prior swing low; a distribution day (≤ −1.25 ATR on ≥ 2×
volume); below the 200-day.

**Not a setup.** No uptrend; extended (more than 2.5 ATR above the 20-day, or at the high); no orderly
pullback.

**Plan.**
- *Zone:* support −0.25 ATR to +0.5 ATR.
- *Trigger:* a daily close above the latest session high. Once triggered, the reference entry is the prior
  high, never a chased price.
- *Stop:* below the lower of the support and the pullback low, minus 0.35 ATR.
- *T1:* the recent swing high, or the 52-week high if the swing high leaves too little room.
- *T2:* the 52-week high, else a 61.8% measured move.
- *Triggered:* the zone was touched within 3 bars and the close is above the prior high on ≥ 1× volume
  with a reversal candle or a strong close.
- *Forming bar:* a daily-close trigger is never confirmed from a bar that has not closed.
- *Chasing:* a warning appears when price is more than 0.75 ATR past the trigger.

**Statuses.**
- *Triggered:* as above.
- *Approaching Entry:* the low of the last 2 bars is within the zone plus 0.25 ATR.
- *Watch:* the setup is valid but price is above the zone, or it is still making new pullback lows.
- *Invalidated:* the close is below the stop of a plan that was active on the prior bar.
- *Avoid:* breakdowns and every hard exclusion.

## 4. Intraday scanner (`scanner/intraday.py`)
- **Inputs.** One session of 1-minute bars (pre-market included) resampled to 5 and 15 minutes. VWAP is
  computed from the regular session.
- **Opening range.** Configurable at 5, 15 or 30 minutes. Nothing is evaluated until the range plus 10
  minutes has passed.
- **Setups.**
  - *VWAP reclaim:* a 5-minute close back above VWAP after a dip below it in the last 30 minutes.
  - *Opening-range breakout retest:* price broke above the range high, the pullback held within −0.75/+0.25
    ATR(5m) of it, and the close is back above.
  - *Orderly VWAP pullback:* the 5-minute EMA9 is above EMA20, the low came within 0.3 ATR(5m) of VWAP,
    and the high of day is above the range high.
- **Breakdown.** Below both VWAP and the opening-range low; lower highs below VWAP; or a gap of 4% or more
  fading below the open and VWAP.
- **Context.** Relative volume is cumulative volume against a typical U-shaped intraday volume curve
  (labelled as an estimate). Market alignment means SPY is above its VWAP; relative strength compares the
  move from the open with SPY's.
- **Plan.**
  - *Stop:* below the pullback low or support, minus 0.25 ATR(5m).
  - *Trigger:* a 5-minute close above the latest 5-minute high, with volume.
  - *Targets:* the nearest of high of day, prior-day high, pre-market high, opening-range measured move,
    20-day high, or prior close plus one daily ATR.
  - *Exit:* by the close.

## 5. News and sentiment (`services/news.py`)
- **Category and impact.** Regex rules map headlines to categories (going concern, offering, guidance,
  earnings, M&A, regulatory/legal, reverse split, analyst, contract, insider, product, management).
  Each category has a default impact.
- **Sentiment.** The provider's sentiment (Polygon insights) when available, otherwise a keyword lexicon.
  The method used is shown on each item.
- **Verification tier.**
  - *verified:* press release or filing.
  - *established:* major outlets and wires.
  - *opinion:* for example Motley Fool or Seeking Alpha.
  - *promotional:* hype language.
  - *unverified:* social media or unknown sources. Unverified items are capped at medium impact.
- **News score (50 = neutral).** Each item adds or subtracts according to impact (high 18, medium 9, low 3)
  multiplied by a trust weight (verified 1.0, established 0.9, opinion 0.25, unverified 0.1,
  promotional 0).
- **Priced in.** The move since publication, in ATRs: ≥ 1 ATR in the news direction counts as "largely
  reflected".
- **Optional AI.** Claude adds a labelled interpretation. It does not change scores, and failures fall back
  to the rules.

## 6. Manipulation-risk score (`services/manipulation.py`)
- **Points by flag.**
  - Micro-cap: 20. Small company: 8.
  - Price below $5: 12, or 18 below $1.
  - Parabolic run (≥ 50% in 5 days or ≥ 100% in 20 days): 25. Sharp run-up (≥ 25% in 5 days): 12.
  - Volume ≥ 5× or ≥ 10× without a verified catalyst: 12 or 20. With a catalyst, a third of that.
  - A move of 15% or more without verified news: 10.
  - Float turnover ≥ 0.5×: 15. Float under 20M shares: 12. Shares outstanding are used when free float is
    unavailable, and the flag is labelled that way.
  - Thin trading: 10. Wide spread: 10.
  - Social mentions at ≥ 10× normal: 15, plus 8 if they are one-sided.
  - Promotional news: 8 per item, up to 15.
  - Dilution filings (S-1/S-3/424B): 12. Late periodic filings: 10. Reverse split: 12.
  - Spike-and-crash, or a reversal after a parabolic run: 10–12.
  - Major negative news: 8.
- **Size adjustment.** Companies ≥ $10B are capped below "elevated". $2–10B scores are multiplied by 0.7.
- **Levels.** Low < 30 ≤ elevated < 55 ≤ high. High is excluded by default and shown only on the Flagged
  page. Inputs that weren't checked are listed as "Not checked".

## 7. Events (`services/events.py`)
- **Earnings blackout.** Swing setups are excluded when earnings fall within 5 trading days; intraday
  within 1. Both are configurable.
- **Macro blackout.** Off by default. Uses FOMC days from `pullback_radar/data/fomc_schedule.json`, which is
  maintained by hand and must be confirmed at federalreserve.gov, plus provider economic calendars.
- **Event-driven decline.** A drop of ≥ 2 ATR or a gap of ≤ −5% in the last 15 sessions that coincides
  (±1 day) with earnings or high-impact negative news. The swing setup is then Avoid with a "review the
  revised outlook" message.

## 8. Ranking (`services/scoring.py`)
- **Components (0–100) and default weights.**
  - Technical: 25%.
  - Trend and relative strength: 20%.
  - Entry quality (R:R and distance to entry): 15%.
  - Volume and liquidity: 15%.
  - News: 10%.
  - Market and sector alignment: 10%.
  - Sentiment quality: 5%. Neutral 50 when no data.
- **Penalties.**
  - Elevated manipulation risk: up to −15.
  - Unknown earnings date: −5.
  - R:R below the minimum: −10 in "warn" mode.
  - Chasing past the trigger: −5.
- **Hard exclusions** (status becomes Avoid): liquidity, high manipulation risk, earnings or event
  blackout, event-driven decline, an invalid plan, and R:R below the minimum in "exclude" mode.
- **To rank**, a setup needs status Watch, Approaching Entry or Triggered, a score ≥ the threshold, and a
  technical component ≥ 50. The last rule stops news or sentiment from rescuing a weak chart.
- **Explanations** cover why the setup qualifies, why the entry is attractive, what confirms it, how it
  could fail, news and event risks, and how it ranks against its neighbours (the largest component
  difference).
- **Not a probability.** The score is a quality rating. No probability of a favourable move is shown,
  because none has been calibrated. Adding one would need a documented model with sample size, horizon and
  calibration reported.

## 9. Risk (`services/risk.py`)
- **Shares** = ⌊(equity × risk% − 2 × commission) ÷ (entry − stop + 2 × slippage)⌋, capped by the maximum
  position %.
- **Reported.** Net profit and net R per target.
- **Warnings.** R:R below the minimum, a zero share count, and stop-gap risk.
- **Daily loss limit.** Configurable at 2% of equity. Hitting it shows "stop trading for today — do not
  increase size to win it back".

## 10. Backtesting (`services/backtest.py`)
- **Replay.** The same scanner functions run bar by bar.
- **Swing entries.** A buy-stop at the trigger, valid for `entry_window_bars` sessions. It is cancelled if
  the stop is hit or price gaps below the stop first. A setup that triggered on its signal bar enters at the
  next open.
- **Intraday entries.** Every `scan_every_minutes` minutes, using 1-minute fills, with a flat exit at 15:55.
- **Exits.** Half at T1 with the stop moved to breakeven, the rest at T2. With only T1, a full exit there.
  Otherwise a time exit after `max_hold_bars`.
- **Conservative fills.**
  - The stop is assumed to fill first when one bar hits both stop and target.
  - A gap through the stop fills at the open.
  - Every fill pays slippage. Commissions are converted to R using your account size and risk %.
- **Reports.**
  - Number of trades, win rate, average win and loss (in R and %), and expectancy before and after costs.
  - Profit factor, max drawdown on a fixed-fractional equity curve, and total return.
  - Breakdowns: in-sample vs out-of-sample (split by date), market regime (SPY bull/bear/sideways on the
    signal date), and setup type.
- **Known limitations.**
  - Survivorship bias: only symbols listed today are tested.
  - The intraday volume curve is a typical profile, and fills ignore queue position and partial fills.
  - The news, sentiment and event filters are not replayed historically. The backtest measures the
    technical rules only.

## 11. Data-provider evaluation

| Provider | Coverage | Latency | Commercial use | Rate limits | Role here |
|---|---|---|---|---|---|
| Polygon.io (Massive) | All U.S. stocks, minute and daily history, reference data, splits, news with sentiment | Real-time or 15-min delayed, depending on plan | Business plans available; individual plans are for personal use | 5/min free, unlimited on paid | Prices, universe, market cap, news |
| Finnhub | Earnings calendar, news; social sentiment and economic calendar on premium | Varies | Commercial licences sold separately | 60/min free | Events, extra news, social |
| SEC EDGAR | All filings | Minutes after filing | Public data | ~10 req/s with a User-Agent | Dilution / late-filing checks |
| Alpaca / IEX (not wired) | IEX-only quotes on free tiers | Real-time IEX | Check terms | — | Possible alternative adapter |
| Yahoo Finance (not used) | Broad | — | No licence for redistribution | Undocumented | Excluded on licensing grounds |

No single free API covers real-time prices, news and sentiment together. Each data category is behind its
own interface in `adapters/base.py`; add a provider by implementing that interface and wiring it in
`providers.py`.
