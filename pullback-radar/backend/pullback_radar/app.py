"""FastAPI application: authentication, scans, research, watchlist, alerts, journal, risk and backtests.

Run: uvicorn pullback_radar.app:create_app --factory --reload  (from the backend directory)"""
from __future__ import annotations

import hashlib
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from . import auth
from .adapters.base import DataUnavailable
from .config import ENV, EnvSettings, ScanSettings
from .db import (Alert, AlertRule, AuthSession, BacktestRun, ScanRun, Trade, User, UserSettings, WatchlistItem,
                 init_db, make_engine)
from .market_calendar import ET, now_et, session_state
from .providers import DataHub, build_hub
from .services import alerts as alert_svc
from .services import backtest as bt
from .services import journal, risk
from .services.scan import analyse_single, chart_data, run_scan

log = logging.getLogger("pullback_radar.api")
COOKIE = "pr_session"
CSRF_HEADER = "x-requested-with"
DISCLAIMER = ("Research and decision-support tool. Not financial advice and no guarantee of outcomes. "
              "No orders are ever placed by this application.")


class Credentials(BaseModel):
    email: str
    password: str


class WatchIn(BaseModel):
    symbol: str
    style: Literal["intraday", "swing"] = "swing"
    note: str | None = None


class RuleIn(BaseModel):
    kind: str
    symbol: str | None = None
    params: dict = Field(default_factory=dict)
    enabled: bool = True


class SizeIn(BaseModel):
    equity: float
    risk_pct: float = 0.5
    entry: float
    stop: float
    slippage_per_share: float = 0.0
    commission_per_trade: float = 0.0
    max_position_pct: float = 100.0
    targets: list[float] = Field(default_factory=list)
    min_reward_risk: float = 2.0


class TradeIn(BaseModel):
    mode: Literal["paper", "actual"]
    symbol: str
    style: Literal["intraday", "swing"]
    strategy: str | None = None
    entry_time: datetime
    entry_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None
    exit_time: datetime | None = None
    exit_price: float | None = Field(default=None, gt=0)
    exit_reason: str | None = None
    fees: float = Field(default=0.0, ge=0)
    rationale: str | None = None
    notes: str | None = None


class PaperIn(BaseModel):
    symbol: str
    style: Literal["intraday", "swing"]
    quantity: float | None = None
    rationale: str | None = None


class BTIn(BaseModel):
    style: Literal["swing", "intraday"] = "swing"
    symbols: list[str] = Field(default_factory=list)
    start: date | None = None
    end: date | None = None
    days_intraday: int = Field(default=10, ge=1, le=60)
    min_reward_risk: float = 2.0
    max_hold_bars: int = Field(default=10, ge=1, le=60)
    entry_window_bars: int = Field(default=3, ge=1, le=10)
    slippage_per_share: float = Field(default=0.02, ge=0)
    commission_per_order: float = Field(default=0.0, ge=0)
    in_sample_pct: float = Field(default=70, ge=10, le=95)
    require_market_uptrend: bool = False


class AppState:
    def __init__(self, env: EnvSettings, hub: DataHub | None = None, db_url: str | None = None):
        self.env = env
        self.hub = hub or build_hub(env)
        self.engine = make_engine(db_url or env.database_url)
        self.Session = init_db(self.engine)
        self.latest: dict[int, dict] = {}
        self.scan_locks: dict[int, threading.Lock] = {}
        self.throttle = auth.LoginThrottle()
        self.stop = threading.Event()
        self.backtests_running: set[int] = set()


def create_app(env: EnvSettings = ENV, hub: DataHub | None = None, db_url: str | None = None,
               start_scheduler: bool | None = None) -> FastAPI:
    state = AppState(env, hub, db_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        th = None
        if start_scheduler if start_scheduler is not None else env.scheduler_enabled:
            from .scheduler import scheduler_loop
            th = threading.Thread(target=scheduler_loop, args=(state,), daemon=True, name="scheduler")
            th.start()
        yield
        state.stop.set()

    app = FastAPI(title="Market Pullback Radar API", version="1.0.0", lifespan=lifespan,
                  description=DISCLAIMER)
    app.state.s = state
    app.add_middleware(CORSMiddleware, allow_origins=env.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # Cookie-authenticated state changes must carry a custom header, which cross-site forms cannot send.
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/") \
                and request.cookies.get(COOKIE) and request.headers.get(CSRF_HEADER) != "pullback-radar":
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
        return await call_next(request)

    # ------------------------------------------------------------------ deps
    def get_db():
        db = state.Session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def current_user(request: Request, db: Session = Depends(get_db)) -> User:
        tok = request.cookies.get(COOKIE)
        authz = request.headers.get("authorization", "")
        if not tok and authz.lower().startswith("bearer "):
            tok = authz[7:]
        if not tok:
            raise HTTPException(401, "Not signed in")
        sess = db.get(AuthSession, auth.token_hash(tok))
        if not sess or sess.expires_at.replace(tzinfo=sess.expires_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(401, "Session expired")
        user = db.get(User, sess.user_id)
        if not user:
            raise HTTPException(401, "Not signed in")
        return user

    def user_settings(db: Session, user: User) -> ScanSettings:
        row = db.get(UserSettings, user.id)
        try:
            return ScanSettings(**(row.data if row else {}))
        except ValidationError:
            return ScanSettings()

    def scan_for(db: Session, user: User, *, force=False, trigger="manual") -> dict:
        lock = state.scan_locks.setdefault(user.id, threading.Lock())
        with lock:
            cached = state.latest.get(user.id)
            if cached and not force:
                age = (datetime.now(ET) - datetime.fromisoformat(cached["generated_at"])).total_seconds()
                if age < (120 if session_state() == "open" else 900):
                    return cached
            if not force:
                run = db.scalars(select(ScanRun).where(ScanRun.user_id == user.id, ScanRun.ok.is_(True))
                                 .order_by(desc(ScanRun.id)).limit(1)).first()
                fresh = run and run.finished_at and (datetime.now(timezone.utc) - run.finished_at.replace(
                    tzinfo=run.finished_at.tzinfo or timezone.utc)).total_seconds() < 300
                if fresh and run.summary.get("settings_hash") == settings_hash(user_settings(db, user)):
                    state.latest[user.id] = run.payload
                    return run.payload
            return execute_scan(state, db, user, user_settings(db, user), trigger)

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    def health():
        return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/api/status")
    def status():
        st = state.hub.status()
        st["session"] = session_state(state.hub.market.now() if state.hub.synthetic else now_et())
        st["disclaimer"] = DISCLAIMER
        return st

    # ------------------------------------------------------------------ auth

    def _login_response(db: Session, user: User, resp: Response):
        tok, h = auth.new_token()
        db.add(AuthSession(token_hash=h, user_id=user.id, expires_at=auth.expiry(env.session_days)))
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", secure=env.cookie_secure,
                        max_age=env.session_days * 86400, path="/")
        return {"id": user.id, "email": user.email, "token": tok}

    @app.post("/api/auth/register")
    def register(body: Credentials, resp: Response, db: Session = Depends(get_db)):
        if not env.allow_registration:
            raise HTTPException(403, "Registration is disabled on this server")
        email = body.email.strip().lower()
        errs = auth.validate_credentials(email, body.password)
        if errs:
            raise HTTPException(422, "; ".join(errs))
        if db.scalars(select(User).where(User.email == email)).first():
            raise HTTPException(409, "An account with this email already exists")
        user = User(email=email, password_hash=auth.hash_password(body.password))
        db.add(user)
        db.flush()
        db.add(UserSettings(user_id=user.id, data=ScanSettings().model_dump()))
        alert_svc.ensure_default_rules(db, user.id)
        return _login_response(db, user, resp)

    @app.post("/api/auth/login")
    def login(body: Credentials, request: Request, resp: Response, db: Session = Depends(get_db)):
        email = body.email.strip().lower()
        key = f"{email}|{request.client.host if request.client else ''}"
        if state.throttle.blocked(key):
            raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
        user = db.scalars(select(User).where(User.email == email)).first()
        if not user or not auth.verify_password(body.password, user.password_hash):
            state.throttle.fail(key)
            raise HTTPException(401, "Invalid email or password")
        state.throttle.reset(key)
        return _login_response(db, user, resp)

    @app.post("/api/auth/logout")
    def logout(request: Request, resp: Response, db: Session = Depends(get_db)):
        tok = request.cookies.get(COOKIE)
        if tok:
            db.execute(delete(AuthSession).where(AuthSession.token_hash == auth.token_hash(tok)))
        resp.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: User = Depends(current_user)):
        return {"id": user.id, "email": user.email}

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings(user: User = Depends(current_user), db: Session = Depends(get_db)):
        return {"settings": user_settings(db, user).model_dump(), "defaults": ScanSettings().model_dump()}

    @app.put("/api/settings")
    def put_settings(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
        try:
            s = ScanSettings(**body)
        except ValidationError as e:
            raise HTTPException(422, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()))
        row = db.get(UserSettings, user.id)
        if row:
            row.data = s.model_dump()
        else:
            db.add(UserSettings(user_id=user.id, data=s.model_dump()))
        state.latest.pop(user.id, None)
        return {"settings": s.model_dump()}

    # ------------------------------------------------------------------ scans
    def _slim(card: dict) -> dict:
        return {k: v for k, v in card.items() if k not in ("levels",)}

    @app.post("/api/scan")
    def scan_now(user: User = Depends(current_user), db: Session = Depends(get_db)):
        res = scan_for(db, user, force=True)
        return summary(res)

    def summary(res: dict) -> dict:
        if not res.get("ok"):
            return res
        out = {k: v for k, v in res.items() if k not in ("intraday", "swing")}
        for st in ("intraday", "swing"):
            if st in res:
                x = res[st]
                out[st] = {"ranked": [_slim(c) for c in x["ranked"]], "watch_only": [_slim(c) for c in x["watch_only"]],
                           "avoid": [_slim(c) for c in x["avoid"]], "flagged_count": len(x["flagged"]),
                           "message": x["message"]}
        return out

    @app.get("/api/scan/latest")
    def latest(user: User = Depends(current_user), db: Session = Depends(get_db)):
        return summary(scan_for(db, user))

    @app.get("/api/market")
    def market_ep(user: User = Depends(current_user), db: Session = Depends(get_db)):
        res = scan_for(db, user)
        if not res.get("ok"):
            return res
        return {"market": res["market"], "synthetic": res["synthetic"], "generated_at": res["generated_at"],
                "session": res["session"], "setup_messages": res["setup_messages"]}

    @app.get("/api/opportunities/{style}")
    def opportunities(style: Literal["intraday", "swing"], user: User = Depends(current_user),
                      db: Session = Depends(get_db)):
        res = scan_for(db, user)
        if not res.get("ok"):
            return res
        if style not in res:
            return {"ok": True, "disabled": True, "message": f"{style.title()} scanning is turned off in Settings."}
        x = res[style]
        return {"ok": True, "style": style, "synthetic": res["synthetic"], "generated_at": res["generated_at"],
                "session": res["session"], "regime": res["market"]["regime"], "message": x["message"],
                "ranked": x["ranked"], "watch_only": x["watch_only"], "avoid": x["avoid"],
                "probability_note": res["probability_note"]}

    @app.get("/api/flagged")
    def flagged(user: User = Depends(current_user), db: Session = Depends(get_db)):
        res = scan_for(db, user)
        if not res.get("ok"):
            return res
        seen, rows = set(), []
        for st in ("swing", "intraday"):
            for c in (res.get(st) or {}).get("flagged", []):
                if c["symbol"] not in seen:
                    seen.add(c["symbol"])
                    rows.append({k: c[k] for k in ("symbol", "name", "price", "price_time", "market_cap", "cap_category",
                                                   "manipulation", "news", "metrics", "freshness", "sector")})
        return {"ok": True, "synthetic": res["synthetic"], "flagged": rows,
                "note": "Shown for review only. These stocks are never mixed into the recommendations feed."}

    @app.get("/api/excluded")
    def excluded(user: User = Depends(current_user), db: Session = Depends(get_db)):
        res = scan_for(db, user)
        return {"ok": res.get("ok", False), "excluded": res.get("excluded", []), "errors": res.get("errors", [])}

    @app.get("/api/news")
    def news_feed(user: User = Depends(current_user), db: Session = Depends(get_db)):
        res = scan_for(db, user)
        if not res.get("ok"):
            return res
        items, seen = [], set()
        for st in ("swing", "intraday"):
            for sym, c in (res.get(st) or {}).get("all", {}).items():
                for n in c.get("news", []):
                    if n["id"] not in seen:
                        seen.add(n["id"])
                        items.append({**n, "symbol": sym, "social": c.get("social")})
        items.sort(key=lambda n: n["published_at"], reverse=True)
        return {"ok": True, "synthetic": res["synthetic"], "company_news": items, "market_news": res["market"]["news"],
                "social_available": state.hub.sentiment is not None,
                "sources_note": "Every item links to its original source. Opinion, promotional and social items are "
                                "labelled and carry little or no weight in scoring."}

    # ------------------------------------------------------------------ research
    @app.get("/api/stocks/{symbol}")
    def research(symbol: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
        symbol = symbol.upper()
        if not state.hub.ready:
            return {"ok": False, "setup_required": True, "messages": state.hub.setup_messages}
        res = analyse_single(state.hub, user_settings(db, user), symbol, env)
        if not res.get("ok"):
            raise HTTPException(404, res.get("error") or "Unavailable")
        try:
            res["chart"] = chart_data(state.hub, symbol)
        except DataUnavailable as e:
            res["chart"] = None
            res["errors"].append({"symbol": symbol, "stage": "chart", "error": str(e)})
        res["watchlist"] = [w.style for w in db.scalars(select(WatchlistItem).where(
            WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol))]
        return res

    # ------------------------------------------------------------------ watchlist

    def _watch_dict(w: WatchlistItem, res: dict | None):
        card = ((res or {}).get(w.style) or {}).get("all", {}).get(w.symbol) if res and res.get("ok") else None
        return {"id": w.id, "symbol": w.symbol, "style": w.style, "note": w.note, "added_at": w.added_at.isoformat(),
                "plan_snapshot": w.plan_snapshot, "last_status": w.last_status, "last_price": w.last_price,
                "last_checked_at": w.last_checked_at.isoformat() if w.last_checked_at else None,
                "current": ({k: card[k] for k in ("status", "price", "price_time", "plan", "score", "freshness",
                                                  "setup_type", "reasons_avoid", "warnings", "manipulation", "events",
                                                  "name")} if card else None)}

    @app.get("/api/watchlist")
    def watchlist(user: User = Depends(current_user), db: Session = Depends(get_db)):
        res = state.latest.get(user.id)
        items = db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id).order_by(WatchlistItem.added_at))
        return {"items": [_watch_dict(w, res) for w in items]}

    @app.post("/api/watchlist")
    def add_watch(body: WatchIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        sym = body.symbol.strip().upper()
        if not sym or len(sym) > 16 or not sym.replace(".", "").replace("-", "").isalnum():
            raise HTTPException(422, "Invalid symbol")
        exists = db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user.id, WatchlistItem.symbol == sym,
                                                        WatchlistItem.style == body.style)).first()
        if exists:
            return _watch_dict(exists, state.latest.get(user.id))
        res = state.latest.get(user.id) or {}
        card = (res.get(body.style) or {}).get("all", {}).get(sym) if res.get("ok") else None
        w = WatchlistItem(user_id=user.id, symbol=sym, style=body.style, note=body.note,
                          plan_snapshot=card.get("plan") if card else None,
                          last_status=card.get("status") if card else None)
        db.add(w)
        db.flush()
        return _watch_dict(w, res)

    @app.delete("/api/watchlist/{item_id}")
    def del_watch(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        w = db.get(WatchlistItem, item_id)
        if not w or w.user_id != user.id:
            raise HTTPException(404, "Not found")
        db.delete(w)
        return {"ok": True}

    # ------------------------------------------------------------------ alerts
    @app.get("/api/alerts")
    def list_alerts(unread_only: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
        q = select(Alert).where(Alert.user_id == user.id)
        if unread_only:
            q = q.where(Alert.read.is_(False))
        rows = db.scalars(q.order_by(desc(Alert.created_at)).limit(200))
        return {"alerts": [{"id": a.id, "symbol": a.symbol, "style": a.style, "kind": a.kind, "title": a.title,
                            "message": a.message, "price": a.price, "price_time": a.price_time, "link": a.link,
                            "synthetic": a.synthetic, "read": a.read, "created_at": a.created_at.isoformat()}
                           for a in rows],
                "unread": len(list(db.scalars(select(Alert.id).where(Alert.user_id == user.id, Alert.read.is_(False)))))}

    @app.post("/api/alerts/{alert_id}/read")
    def read_alert(alert_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        a = db.get(Alert, alert_id)
        if not a or a.user_id != user.id:
            raise HTTPException(404, "Not found")
        a.read = True
        return {"ok": True}

    @app.post("/api/alerts/read-all")
    def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
        for a in db.scalars(select(Alert).where(Alert.user_id == user.id, Alert.read.is_(False))):
            a.read = True
        return {"ok": True}


    @app.get("/api/alert-rules")
    def rules(user: User = Depends(current_user), db: Session = Depends(get_db)):
        alert_svc.ensure_default_rules(db, user.id)
        return {"kinds": alert_svc.KINDS, "rules": [
            {"id": r.id, "kind": r.kind, "symbol": r.symbol, "params": r.params, "enabled": r.enabled}
            for r in db.scalars(select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.id))]}

    @app.post("/api/alert-rules")
    def add_rule(body: RuleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if body.kind not in alert_svc.KINDS:
            raise HTTPException(422, "Unknown alert kind")
        if body.kind == "price_cross":
            if not body.symbol or not ({"above", "below"} & set(body.params)):
                raise HTTPException(422, "price_cross needs a symbol and an 'above' or 'below' level")
        r = AlertRule(user_id=user.id, kind=body.kind, symbol=body.symbol.upper() if body.symbol else None,
                      params=body.params, enabled=body.enabled)
        db.add(r)
        db.flush()
        return {"id": r.id}

    @app.patch("/api/alert-rules/{rule_id}")
    def patch_rule(rule_id: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
        r = db.get(AlertRule, rule_id)
        if not r or r.user_id != user.id:
            raise HTTPException(404, "Not found")
        if "enabled" in body:
            r.enabled = bool(body["enabled"])
        if "params" in body and isinstance(body["params"], dict):
            r.params = body["params"]
        return {"ok": True}

    @app.delete("/api/alert-rules/{rule_id}")
    def del_rule(rule_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        r = db.get(AlertRule, rule_id)
        if not r or r.user_id != user.id:
            raise HTTPException(404, "Not found")
        db.delete(r)
        return {"ok": True}

    # ------------------------------------------------------------------ risk

    @app.post("/api/risk/position-size")
    def size(body: SizeIn, user: User = Depends(current_user)):
        return risk.position_size(**body.model_dump())

    @app.get("/api/risk/daily-loss")
    def daily_loss(user: User = Depends(current_user), db: Session = Depends(get_db)):
        s = user_settings(db, user)
        trades = list(db.scalars(select(Trade).where(Trade.user_id == user.id)))
        today = (state.hub.market.now() if state.hub.synthetic else now_et()).date()
        out = {}
        for mode in ("actual", "paper"):
            out[mode] = risk.daily_loss_status(journal.realized_today([t for t in trades if t.mode == mode], today),
                                               s.account_equity, s.daily_loss_limit_pct)
        return out

    # ------------------------------------------------------------------ journal

    def _trade_dict(t: Trade):
        return {"id": t.id, "mode": t.mode, "symbol": t.symbol, "style": t.style, "strategy": t.strategy,
                "side": t.side, "status": t.status, "entry_time": t.entry_time.isoformat(),
                "entry_price": t.entry_price, "quantity": t.quantity, "stop": t.stop, "target1": t.target1,
                "target2": t.target2, "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                "exit_price": t.exit_price, "exit_reason": t.exit_reason, "fees": t.fees, "rationale": t.rationale,
                "notes": t.notes, "screenshot": f"/api/trades/{t.id}/screenshot" if t.screenshot else None,
                "pnl": journal.trade_pnl(t), "r_multiple": journal.trade_r(t), "synthetic_data": t.synthetic_data,
                "setup_snapshot": t.setup_snapshot}

    @app.get("/api/trades")
    def trades(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = list(db.scalars(select(Trade).where(Trade.user_id == user.id).order_by(desc(Trade.entry_time))))
        return {"trades": [_trade_dict(t) for t in rows], "stats": journal.journal_stats(rows)}

    @app.post("/api/trades")
    def add_trade(body: TradeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if body.stop is not None and body.stop >= body.entry_price:
            raise HTTPException(422, "Stop must be below the entry for a long trade")
        t = Trade(user_id=user.id, **body.model_dump(), status="closed" if body.exit_price else "open",
                  synthetic_data=False if body.mode == "actual" else state.hub.synthetic)
        t.symbol = t.symbol.upper()
        db.add(t)
        db.flush()
        return _trade_dict(t)

    @app.patch("/api/trades/{trade_id}")
    def patch_trade(trade_id: int, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
        t = db.get(Trade, trade_id)
        if not t or t.user_id != user.id:
            raise HTTPException(404, "Not found")
        allowed = {"strategy", "stop", "target1", "target2", "exit_price", "exit_time", "exit_reason", "fees",
                   "rationale", "notes", "quantity"}
        for k, v in body.items():
            if k not in allowed:
                continue
            if k == "exit_time" and v:
                v = datetime.fromisoformat(v)
            setattr(t, k, v)
        if t.exit_price is not None and t.exit_time is None:
            t.exit_time = datetime.now(timezone.utc)
        t.status = "closed" if t.exit_price is not None else "open"
        return _trade_dict(t)

    @app.delete("/api/trades/{trade_id}")
    def del_trade(trade_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        t = db.get(Trade, trade_id)
        if not t or t.user_id != user.id:
            raise HTTPException(404, "Not found")
        db.delete(t)
        return {"ok": True}

    @app.post("/api/trades/{trade_id}/screenshot")
    async def upload_shot(trade_id: int, file: UploadFile = File(...), user: User = Depends(current_user),
                          db: Session = Depends(get_db)):
        t = db.get(Trade, trade_id)
        if not t or t.user_id != user.id:
            raise HTTPException(404, "Not found")
        ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(file.content_type or "")
        if not ext:
            raise HTTPException(415, "Upload a PNG, JPEG or WebP image")
        data = await file.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(413, "Image larger than 5 MB")
        env.upload_dir.mkdir(parents=True, exist_ok=True)
        name = f"{user.id}-{trade_id}-{uuid.uuid4().hex}{ext}"
        (env.upload_dir / name).write_bytes(data)
        t.screenshot = name
        return {"ok": True}

    @app.get("/api/trades/{trade_id}/screenshot")
    def get_shot(trade_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        t = db.get(Trade, trade_id)
        if not t or t.user_id != user.id or not t.screenshot:
            raise HTTPException(404, "Not found")
        p = (env.upload_dir / Path(t.screenshot).name)
        if not p.exists():
            raise HTTPException(404, "File missing")
        return FileResponse(p)


    @app.post("/api/paper/from-setup")
    def paper_from_setup(body: PaperIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        """Record a simulated entry at the latest observed quote using the setup's stop and targets."""
        sym = body.symbol.upper()
        res = scan_for(db, user)
        card = ((res.get(body.style) or {}).get("all", {}).get(sym)) if res.get("ok") else None
        if not card or not card.get("plan"):
            raise HTTPException(409, "No current trade plan for this symbol — run a scan first")
        if card["status"] != "Triggered":
            raise HTTPException(409, f"Setup status is {card['status']} — a paper entry needs the confirmation "
                                     f"trigger first ({card['plan']['trigger_text'] if card.get('plan') else 'no plan'})")
        try:
            q = state.hub.market.quote(sym)
        except DataUnavailable as e:
            raise HTTPException(503, f"No current quote: {e}")
        plan = card["plan"]
        if q.price <= plan["stop"]:
            raise HTTPException(409, "Price is at or below the stop — the setup is invalid")
        qty = body.quantity or (card.get("position_size") or {}).get("shares") or 0
        if qty <= 0:
            raise HTTPException(422, "Position size is zero for this risk budget; enter a quantity")
        t = Trade(user_id=user.id, mode="paper", symbol=sym, style=body.style, strategy=card.get("setup_type"),
                  entry_time=q.timestamp.astimezone(timezone.utc), entry_price=q.price, quantity=qty,
                  stop=plan["stop"], target1=plan["target1"], target2=plan.get("target2"),
                  rationale=body.rationale or plan["conditional_text"],
                  setup_snapshot={"plan": plan, "status": card["status"], "score": card["score"]["score"],
                                  "quote_source": q.source, "quote_time": q.timestamp.isoformat()},
                  synthetic_data=state.hub.synthetic)
        db.add(t)
        db.flush()
        return {**_trade_dict(t), "note": "Paper trade recorded at the latest observed quote. No order was sent."}

    # ------------------------------------------------------------------ backtests

    @app.post("/api/backtests")
    def start_backtest(body: BTIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
        if not state.hub.ready:
            raise HTTPException(503, "Data providers are not configured")
        syms = [s.upper() for s in body.symbols][:50]
        if not syms:
            res = state.latest.get(user.id) or {}
            syms = [c["symbol"] for st in ("swing", "intraday") for c in (res.get(st) or {}).get("all", {}).values()
                    if c.get("is_setup")][:20] if res.get("ok") else []
        if not syms:
            raise HTTPException(422, "Give symbols or run a scan first")
        mnow = (state.hub.market.now() if state.hub.synthetic else now_et()).date()
        end = body.end or mnow
        start = body.start or (end - timedelta(days=365))
        s = user_settings(db, user)
        params = bt.BTParams(style=body.style, min_reward_risk=body.min_reward_risk, entry_window_bars=body.entry_window_bars,
                             max_hold_bars=body.max_hold_bars, slippage_per_share=body.slippage_per_share,
                             commission_per_order=body.commission_per_order, in_sample_pct=body.in_sample_pct,
                             require_market_uptrend=body.require_market_uptrend, risk_pct=s.risk_pct_per_trade,
                             account_equity=s.account_equity)
        run = BacktestRun(user_id=user.id, style=body.style, params={**body.model_dump(mode="json"), "symbols": syms},
                          result={"ok": None, "status": "running"}, synthetic=state.hub.synthetic)
        db.add(run)
        db.flush()
        run_id = run.id
        db.commit()

        def work():
            try:
                result = bt.run_backtest(state.hub, syms, params, start, end, days_intraday=body.days_intraday,
                                         or_minutes=s.opening_range_minutes)
            except Exception as e:  # noqa: BLE001
                log.exception("backtest failed")
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            with state.Session() as d2:
                r = d2.get(BacktestRun, run_id)
                r.result = {**result, "status": "done"}
                d2.commit()
            state.backtests_running.discard(run_id)

        state.backtests_running.add(run_id)
        threading.Thread(target=work, daemon=True).start()
        return {"id": run_id, "status": "running"}

    @app.get("/api/backtests")
    def list_bt(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.scalars(select(BacktestRun).where(BacktestRun.user_id == user.id).order_by(desc(BacktestRun.id)).limit(30))
        return {"runs": [{"id": r.id, "style": r.style, "params": r.params, "created_at": r.created_at.isoformat(),
                          "synthetic": r.synthetic, "status": r.result.get("status"),
                          "overall": r.result.get("overall"), "ok": r.result.get("ok")} for r in rows]}

    @app.get("/api/backtests/{run_id}")
    def get_bt(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        r = db.get(BacktestRun, run_id)
        if not r or r.user_id != user.id:
            raise HTTPException(404, "Not found")
        return {"id": r.id, "style": r.style, "params": r.params, "created_at": r.created_at.isoformat(),
                "synthetic": r.synthetic, "result": r.result}

    return app


def settings_hash(s: ScanSettings) -> str:
    return hashlib.sha256(s.model_dump_json().encode()).hexdigest()[:16]


def execute_scan(state: AppState, db: Session, user: User, settings: ScanSettings, trigger: str) -> dict:
    run = ScanRun(user_id=user.id, mode=state.hub.mode, synthetic=state.hub.synthetic, trigger=trigger)
    db.add(run)
    db.flush()
    res = run_scan(state.hub, settings, state.env)
    run.finished_at = datetime.now(timezone.utc)
    run.ok = bool(res.get("ok"))
    if res.get("ok"):
        run.summary = {st: {"ranked": len(res[st]["ranked"]), "flagged": len(res[st]["flagged"])}
                       for st in ("intraday", "swing") if st in res}
        run.summary["settings_hash"] = settings_hash(settings)
        run.payload = res
        state.latest[user.id] = res
        alert_svc.evaluate_user(db, user.id, res)
        _update_paper(state, db, user.id)
        old = db.scalars(select(ScanRun.id).where(ScanRun.user_id == user.id).order_by(desc(ScanRun.id)).offset(20))
        ids = list(old)
        if ids:
            db.execute(delete(ScanRun).where(ScanRun.id.in_(ids)))
    else:
        run.summary = {"messages": res.get("messages")}
    db.flush()
    return res


def _update_paper(state: AppState, db: Session, user_id: int):
    now = state.hub.market.now() if state.hub.synthetic else now_et()
    for t in db.scalars(select(Trade).where(Trade.user_id == user_id, Trade.mode == "paper", Trade.status == "open")):
        try:
            q = state.hub.market.quote(t.symbol)
        except DataUnavailable:
            continue
        note = journal.update_paper_trade(t, q.price, q.timestamp, now)
        if note:
            t.notes = ((t.notes or "") + f"\n[{q.timestamp:%Y-%m-%d %H:%M} ET] {note}").strip()

