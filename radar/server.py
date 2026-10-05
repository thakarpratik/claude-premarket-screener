"""FastAPI server: JSON API + the static card UI. Run with:  python -m app.server"""
import threading
import time
import webbrowser

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import engine, store
from .config import REFRESH_MINUTES, ROOT

store.init()
app = FastAPI(title="Stock Move Screener")


@app.get("/api/snapshot")
def snapshot():
    return {"snapshot": engine.snapshot(), "status": engine.STATUS}


@app.get("/api/status")
def status():
    return engine.STATUS


@app.post("/api/refresh")
def refresh():
    if engine.STATUS["running"]:
        return {"started": False, "reason": "already running"}
    threading.Thread(target=engine.refresh, daemon=True).start()
    return {"started": True}


@app.get("/api/stock/{sym}")
def stock(sym: str):
    try:
        return engine.detail(sym)
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/performance")
def performance():
    return engine.performance()


@app.get("/api/watchlist")
def watchlist():
    return store.watchlist()


@app.post("/api/watchlist/{sym}")
def watch_add(sym: str):
    store.watch_add(sym)
    return store.watchlist()


@app.delete("/api/watchlist/{sym}")
def watch_remove(sym: str):
    store.watch_remove(sym)
    return store.watchlist()


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


def scheduler():
    """Refresh every REFRESH_MINUTES while the market is open/pre-market, hourly otherwise.
    The first refresh after the close grades the day and retrains the model."""
    while True:
        engine.refresh()
        state = engine.session_state()
        time.sleep(REFRESH_MINUTES * 60 if state in ("open", "pre") else 3600)


def main():
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
