# Pre-market screen

A daily pre-market dashboard for U.S. stocks. It scans the S&P 500, S&P 400,
Nasdaq-100 and `watchlist.txt` for unusual moves before the open and writes an
HTML page to `reports/latest.html`. A learning loop grades each day's setups
against real prices and adjusts the scoring weights.

Research screen only, not investment advice.

## Setup

```
pip install -r requirements.txt
python premarket_screen.py              # build once and open in the browser
python premarket_screen.py --watch 5    # rebuild every 5 min until 09:35 ET
python premarket_screen.py --no-open    # build without opening a browser
```

## What's on the page

1. **Learning:** how yesterday's setups did, calibration by score band, and weight changes.
2. **Top long setups:** 0–100 scores with trigger, invalidation and evidence for and against.
   Stocks under $2B market cap are never picked.
3. **Market dashboard:** futures, VIX, yields, dollar, commodities, crypto and global indices.
4. **Sector and factor ETFs**, pre-market headlines, the unusual-movers table, evidence cards and calendars.

## Learning loop (`learning.py`)

- Each run journals every scored setup to `journal/picks.jsonl`.
- The next run replays each setup on that day's 5-minute bars. Entry is at the trigger,
  exit is at the invalidation level or the close, and the result is recorded in R.
- Weights take a capped step (max 3 points per day) toward what worked. Predictions that
  were completely wrong get twice the correction.
- Delete `journal/weights.json` to reset to the starting weights.

## Scheduling (Windows)

`run_premarket.bat` is run by the Task Scheduler task `PremarketScreen`: weekdays at
8:30 AM, waking the PC and running on battery, and catching up if a run was missed.
Output goes to `reports/run.log`.

`run_premarket.bat` has a hard-coded path to `python.exe`. Edit it if Python lives
somewhere else.

## Data sources

Yahoo Finance (via yfinance), Nasdaq public APIs (market caps, calendars, Nasdaq-100),
Wikipedia (index members), and Yahoo and Google News RSS. Free data can be delayed or
incomplete, and the page labels what couldn't be verified.
