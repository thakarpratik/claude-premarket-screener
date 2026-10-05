# Move Radar — US stock big-move screener

Shows US stocks as cards ranked by their **measured chance of a ≥3% move next session**. Each card also covers what happened today and why, the factors behind the odds, and a fact-based buy-side checklist. Every prediction is logged, graded after the next close, and fed back into a daily retrain.

## Run

Double-click `run.bat` (or run `python -m radar.server`). The app opens at http://127.0.0.1:8765.
The first scan takes about a minute: it downloads two years of data and trains the model. While the app is running, it refreshes every 15 minutes during market hours and hourly at other times.

### Auto-start at logon

The Windows task **MoveRadar** starts the app hidden whenever you log in (no window, no browser). It refreshes,
learns and uploads to the online site in the background, and logs to `data/server.log`.
`run.bat` and http://127.0.0.1:8765 still work: if the app is already running, `run.bat` just opens the page.
To stop auto-start: Task Scheduler → MoveRadar → Disable. `start_background.bat` starts it hidden by hand.

## What each card shows

| Section | Source |
|---|---|
| Price, prev close, change, gap, range, volume | Yahoo daily bars (live during the session) |
| Big-move chance + "× average odds" | Logistic model trained on ~50k stock-days |
| Direction lean | Separate model, shown only with its tested accuracy ("no proven edge" when it doesn't beat the baseline) |
| Why it moved | Stock vs. sector ETF vs. S&P 500, volume, earnings, recent headlines that name the company |
| Drivers | The 3 factors raising this stock's odds most today |
| Signals bar | Checklist: trend (50/200-day), relative strength, analysts, target, valuation, growth, short interest, volume, earnings risk |

Click a card to open the detail view: chart, options-implied move, full checklist, every factor's push on the odds, news, and this stock's own prediction record.

## Pump-and-dump protection

Every stock is checked against the SEC's published pump-and-dump warning signs:

- a tiny company or penny-stock price
- a sudden price spike on exploding volume
- the whole float trading in one day, or a very small float
- losses or little revenue
- no analyst coverage and few institutional owners
- a reverse split in the last year
- a big move with no company news
- an earlier spike-and-crash pattern

Each flag shows the measured value, and together they produce a 0–100 risk score. Companies over $10B are too big to pump, so they're capped at low risk.
High-risk stocks are **hidden by default** (use the "Hide pump-and-dump risks" toggle), except ones on your watchlist, which stay visible with the warning.
The detail view also shows what actually happened after past spikes in the app's own price history. That sample grows every day, because Yahoo's small-cap gainers are downloaded and studied.

## How it learns

1. Every refresh logs a prediction per stock (`data/screener.db`).
2. After the next session closes, each prediction is graded with the real move.
3. Once a day the model retrains on the newest data and:
   - re-picks how much weight recent days get (tested on the last 20 days, held out of training);
   - gives calls that were completely wrong (off by more than 60 points) 3× weight.
4. The **Track record & learning** tab shows the live hit rate, calibration, misses, weights and the retraining log.

If the app isn't run for a few days, it catches up on the next run, because grading and training use the full price history.

## View it from anywhere (private online site)

The engine runs on this PC. After every refresh it uploads one JSON bundle (screen, track record, and detail views for the
top stocks + watchlist) to a **private** Vercel Blob store. The Next.js site in this repo (`app/`, `lib/`, `middleware.ts`)
serves the same page in read-only mode, behind a password.

- `radar/` – Python engine (data, model, learning, pump screen, publishing)
- `static/` – the page (used both locally and online)
- `app/`, `lib/`, `middleware.ts` – the online site (login + data route)

Setup (one time):
1. Vercel project connected to this repo, with a private Blob store connected and `SITE_PASSWORD` / `AUTH_SECRET` set.
2. Put the Blob store's read-write token in `private.env` on this PC (see `private.env.example`). It is never committed.
3. Pushing to `main` redeploys the site.

The site shows whatever the PC last uploaded. If the PC is off, it shows the last upload and its time.

## Settings

Edit `radar/config.py`: big-move threshold, universe, refresh interval, miss weighting.
To reset learning, delete the `data/` folder.

Research tool, not financial advice. Probabilities are statistics, not certainties.
