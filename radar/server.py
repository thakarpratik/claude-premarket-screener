"""FastAPI server: JSON API + the static card UI. Run with:  python -m radar.server"""
import socket
import sys
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


URL = "http://127.0.0.1:8765"


def already_running():
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 8765)) == 0


def main():
    """python -m radar.server            -> start (or just open the page if it's already running)
       python -m radar.server --background -> no browser, output to data/server.log (used at logon)"""
    background = "--background" in sys.argv
    if already_running():
        if not background:
            webbrowser.open(URL)
        return
    if background:
        log = open(ROOT / "data" / "server.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log
        print(f"===== started {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
    else:
        threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
    threading.Thread(target=scheduler, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
