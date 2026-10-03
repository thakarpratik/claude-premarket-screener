# Claude Premarket Screener

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

## Private app (phone)

The Next.js app at the repository root is the private site. After each screener
run, `publish.py` uploads `latest.html` to a private Blob store. The phone
never sees that file directly. Add the site to your home screen and it opens
full screen.

The setup card button opens TradingView for that ticker. To point it at a
broker, copy `broker.example.json` to `broker.json` and set `url` to an
`https://` address that contains `{ticker}`.

Deploy once:

1. In Vercel, import this repository. Leave the root directory as the repository
   root, and set the framework to Next.js if it still says Python.
2. Create a Blob store and set its access to Private. Connect it to the project.
3. On the project, set `SITE_PASSWORD` and `AUTH_SECRET` (at least 16 characters).
   The Blob connection supplies `BLOB_READ_WRITE_TOKEN`.
4. Copy that token into `private.env` on this PC (`private.env.example` shows the line).
5. Deploy, open the site, and use Add to Home Screen.

The scan still runs on this PC. If the PC is asleep, the phone shows the last
upload. A wrong password stays on the login page. Log out is on the dashboard.

## Data sources

Yahoo Finance (via yfinance), Nasdaq public APIs (market caps, calendars, Nasdaq-100),
Wikipedia (index members), and Yahoo and Google News RSS. Free data can be delayed or
incomplete, and the page labels what couldn't be verified.
