"""SQLite persistence: prediction log (with graded outcomes), model runs, watchlist, small caches."""
import json
import sqlite3
import threading
import time

from .config import DB_PATH

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            date TEXT, symbol TEXT,
            p_big REAL, p_up REAL, price REAL,
            features TEXT, model_id TEXT, made_at TEXT,
            next_date TEXT, actual_ret REAL, big INTEGER, up INTEGER, evaluated_at TEXT,
            PRIMARY KEY (date, symbol));
        CREATE TABLE IF NOT EXISTS model_runs (
            id TEXT PRIMARY KEY, run_at TEXT, trained_through TEXT, n_train INTEGER,
            half_life REAL, dir_half_life REAL, misses_upweighted INTEGER,
            holdout_logloss REAL, baseline_logloss REAL, holdout_auc REAL,
            dir_holdout_acc REAL, dir_baseline_acc REAL, base_rate REAL, weights TEXT, dir_weights TEXT);
        CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY, added_at TEXT);
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, updated REAL);
        """)


# ---------- cache ----------
def cache_get(key, max_age_s):
    with _lock, _conn() as c:
        r = c.execute("SELECT value, updated FROM cache WHERE key=?", (key,)).fetchone()
    if r and time.time() - r["updated"] < max_age_s:
        return json.loads(r["value"])
    return None


def cache_set(key, value):
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value, default=str), time.time()))


# ---------- watchlist ----------
def watchlist():
    with _lock, _conn() as c:
        return [r["symbol"] for r in c.execute("SELECT symbol FROM watchlist ORDER BY added_at")]


def watch_add(sym):
    with _lock, _conn() as c:
        c.execute("INSERT OR IGNORE INTO watchlist VALUES (?, datetime('now'))", (sym.upper(),))


def watch_remove(sym):
    with _lock, _conn() as c:
        c.execute("DELETE FROM watchlist WHERE symbol=?", (sym.upper(),))


# ---------- predictions ----------
def save_predictions(rows):
    """rows: dicts with date, symbol, p_big, p_up, price, features, model_id, made_at.
    Same (date, symbol) is overwritten until graded, so the last snapshot of the day is what gets scored."""
    with _lock, _conn() as c:
        c.executemany("""
        INSERT INTO predictions (date, symbol, p_big, p_up, price, features, model_id, made_at)
        VALUES (:date, :symbol, :p_big, :p_up, :price, :features, :model_id, :made_at)
        ON CONFLICT(date, symbol) DO UPDATE SET
            p_big=excluded.p_big, p_up=excluded.p_up, price=excluded.price, features=excluded.features,
            model_id=excluded.model_id, made_at=excluded.made_at
        WHERE predictions.evaluated_at IS NULL""", rows)


def pending_predictions():
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT date, symbol, p_big, p_up FROM predictions WHERE evaluated_at IS NULL")]


def grade(date, symbol, next_date, actual_ret, big, up):
    with _lock, _conn() as c:
        c.execute("""UPDATE predictions SET next_date=?, actual_ret=?, big=?, up=?, evaluated_at=datetime('now')
                     WHERE date=? AND symbol=?""", (next_date, actual_ret, big, up, date, symbol))


def graded(limit_days=None):
    q = "SELECT * FROM predictions WHERE evaluated_at IS NOT NULL"
    if limit_days:
        q += f" AND date >= date('now', '-{int(limit_days)} days')"
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY date")]


def symbol_history(sym, n=30):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT date, p_big, p_up, price, actual_ret, big, up FROM predictions WHERE symbol=? ORDER BY date DESC LIMIT ?",
            (sym, n))]


def latest_features(sym):
    with _lock, _conn() as c:
        r = c.execute("SELECT features FROM predictions WHERE symbol=? ORDER BY date DESC LIMIT 1", (sym,)).fetchone()
    return json.loads(r["features"]) if r and r["features"] else None


# ---------- model runs ----------
def save_model_run(run):
    with _lock, _conn() as c:
        c.execute("""INSERT OR REPLACE INTO model_runs VALUES
            (:id,:run_at,:trained_through,:n_train,:half_life,:dir_half_life,:misses_upweighted,
             :holdout_logloss,:baseline_logloss,:holdout_auc,:dir_holdout_acc,:dir_baseline_acc,:base_rate,
             :weights,:dir_weights)""", run)


def model_runs(n=60):
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM model_runs ORDER BY run_at DESC LIMIT ?", (n,))]
