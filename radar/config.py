"""Central settings. Edit these to tune the screener."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "screener.db"
MODEL_PATH = DATA_DIR / "model.json"
HIST_CACHE = DATA_DIR / "history.pkl"

# A "large move" = next session's close-to-close change of at least this many percent (up or down).
BIG_MOVE_PCT = 3.0

# How much daily history the model learns from.
HISTORY_PERIOD = "2y"

# Background refresh interval while the US market is open (minutes).
REFRESH_MINUTES = 15

# Days held out to choose how strongly the model favours recent data (re-chosen every day).
HOLDOUT_DAYS = 20

# A prediction counts as "completely off" when |outcome - probability| exceeds this.
# Such rows get extra weight when the model retrains.
MISS_THRESHOLD = 0.6
MISS_WEIGHT = 3.0

# Movers pulled live from Yahoo's screeners (only names above this market cap / price).
MOVER_MIN_MARKET_CAP = 500e6
MOVER_MIN_PRICE = 3.0
MOVERS_PER_LIST = 15

# How many top cards get live news headlines fetched.
NEWS_TOP_N = 45

MARKET_TICKERS = ["SPY", "^VIX"]
SECTOR_ETF = {
    "Technology": "XLK", "Communication Services": "XLC", "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP", "Financial Services": "XLF", "Healthcare": "XLV",
    "Energy": "XLE", "Industrials": "XLI", "Basic Materials": "XLB",
    "Real Estate": "XLRE", "Utilities": "XLU",
}

# Core universe the model trains on (watchlist names are added automatically).
UNIVERSE = """
AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO AMD NFLX ORCL CRM ADBE INTC QCOM MU TXN AMAT LRCX KLAC
MRVL ARM SMCI PLTR SNOW CRWD PANW ZS NET DDOG SHOP UBER ABNB COIN HOOD XYZ PYPL SOFI AFRM RBLX
RIVN LCID NIO F GM JPM BAC WFC C GS MS SCHW V MA AXP BRK-B UNH LLY JNJ PFE MRK ABBV MRNA BMY
AMGN GILD VRTX REGN ISRG XOM CVX OXY COP SLB WMT COST TGT HD LOW NKE SBUX MCD DIS BA CAT DE GE
LMT RTX UPS FDX DAL UAL CCL MARA RIOT MSTR SMR OKLO IONQ RGTI SOUN CVNA DKNG ROKU SNAP PINS TTD
ENPH FSLR CELH ON WDC STX DELL HPE ANET VRT APP
""".split()
