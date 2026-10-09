"""Scan orchestration: market overview -> universe -> per-symbol analysis -> ranked cards.

Every card carries the data timestamps and sources it was built from. A symbol whose critical data is
missing is reported in `errors`/`excluded`, never filled in."""
from __future__ import annotations

import logging
import math
import time as _time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..adapters.base import DataUnavailable, NotConfigured, TickerInfo
from ..adapters.sectors import INDEX_PROXIES, SECTOR_ETFS
from ..config import ENV, EnvSettings, ScanSettings
from ..market_calendar import ET, current_or_last_session, freshness, last_completed_session, now_et, session_state
from ..providers import DataHub
from ..scanner.intraday import analyze_intraday
from ..scanner.swing import analyze_swing
from ..universe import cap_category, liquidity_eligibility, security_eligibility
from . import events as ev_mod
from . import manipulation, market, news as news_mod, risk, scoring

log = logging.getLogger("pullback_radar.scan")
HISTORY_DAYS = 420
NO_SETUPS = "No qualifying setups right now."


def jsonable(o):
    """Plain-JSON copy: numpy scalars to Python, NaN/inf to None, timestamps to ISO strings."""
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    return o


class ScanContext:
    def __init__(self, hub: DataHub, settings: ScanSettings, now: datetime | None = None):
        self.hub, self.s = hub, settings
        self.now = (now or (hub.market.now() if hub.synthetic else now_et())).astimezone(ET)
        self.session = session_state(self.now)
        self.today = self.now.date()
        self.daily_cache: dict[str, pd.DataFrame] = {}
        self.errors: list[dict] = []

    def daily(self, sym: str) -> pd.DataFrame | None:
        if sym not in self.daily_cache:
            try:
                raw = self.hub.market.daily_bars(sym, self.today - timedelta(days=HISTORY_DAYS), self.today)
                f = ind.add_daily_indicators(raw) if len(raw) else None
                if f is not None:
                    f.attrs.update(raw.attrs)
                self.daily_cache[sym] = f
            except DataUnavailable as e:
                self.errors.append({"symbol": sym, "stage": "daily bars", "error": str(e)})
                self.daily_cache[sym] = None
        return self.daily_cache[sym]


def market_overview(ctx: ScanContext, universe_frames: dict[str, pd.DataFrame] | None = None) -> dict:
    benches = {k: market.benchmark_summary(ctx.daily(k)) for k in INDEX_PROXIES}
    sectors = market.sector_table({etf: ctx.daily(etf) for etf in SECTOR_ETFS.values()})
    vol = market.realized_vol(ctx.daily("SPY"))
    br = market.breadth(universe_frames or {})
    reg = market.regime(benches, br, vol)
    news_items = []
    for p in ctx.hub.news:
        try:
            news_items += [news_mod.analyse(n) for n in p.market_news(ctx.now - timedelta(days=2), 10)]
        except DataUnavailable as e:
            ctx.errors.append({"symbol": None, "stage": f"market news ({p.name})", "error": str(e)})
    econ = []
    if ctx.hub.events:
        try:
            econ = ctx.hub.events.economic(ctx.today, ctx.today + timedelta(days=10))
        except DataUnavailable as e:
            ctx.errors.append({"symbol": None, "stage": "economic calendar", "error": str(e)})
    econ += [e for e in ev_mod.load_fomc() if ctx.today <= e.date <= ctx.today + timedelta(days=21)]
    if ctx.session == "open":
        cond = "Regular session open."
    elif ctx.session == "pre":
        cond = "Pre-market: thin liquidity and wide spreads; intraday setups form after the opening range."
    elif ctx.session == "after":
        cond = "After-hours: the regular session has closed; levels are from today's close."
    else:
        cond = "Market closed. Levels are from the last session and may not be valid at the next open."
    spy = ctx.daily("SPY")
    return {
        "indices": market.index_rows(benches), "sectors": sectors, "volatility": {
            "spy_realized_20d": vol, "label": None if vol is None else "high" if vol >= 30 else "elevated" if vol >= 20
            else "normal" if vol >= 12 else "low", "source": "SPY 20-day realized volatility (VIX not in feed)"},
        "breadth": br, "regime": reg, "news": sorted(news_items, key=lambda x: x["published_at"], reverse=True)[:10],
        "economic_events": [e.to_dict() for e in sorted(econ, key=lambda e: e.date)],
        "session": ctx.session, "conditions": cond,
        "as_of": spy.index[-1].isoformat() if spy is not None and len(spy) else None,
    }


def select_universe(ctx: ScanContext, env: EnvSettings) -> tuple[list[TickerInfo], list[dict]]:
    excluded = []
    ref = ctx.hub.reference
    if ctx.hub.synthetic:
        infos = ref.list_universe()
    elif not ctx.hub.market.supports_grouped and not env.universe_symbols:
        infos = ref.list_universe()  # providers without a full listing use their configured symbol list
    elif env.universe_symbols:
        infos = []
        for sym in env.universe_symbols:
            try:
                infos.append(ref.ticker_info(sym))
            except DataUnavailable as e:
                excluded.append({"symbol": sym, "reasons": [str(e)]})
    else:
        infos = _live_prefilter(ctx, env, excluded)
    keep = []
    for info in infos:
        reasons = security_eligibility(info, ctx.s)
        if reasons:
            excluded.append({"symbol": info.symbol, "name": info.name, "reasons": reasons})
        else:
            keep.append(info)
    return keep, excluded


def _live_prefilter(ctx: ScanContext, env: EnvSettings, excluded: list) -> list[TickerInfo]:
    """Cheap first pass with grouped daily bars, then reference data only for liquid names."""
    listing = {t.symbol: t for t in ctx.hub.reference.list_universe() if t.security_type == "CS"}
    d, frames = last_completed_session(ctx.now), []
    while len(frames) < 20:
        g = ctx.hub.market.grouped_daily(d)
        if g is not None:
            frames.append(g)
        d = d - timedelta(days=1)
        if (ctx.today - d).days > 40:
            break
    if not frames:
        raise DataUnavailable("Grouped daily bars unavailable; set UNIVERSE_SYMBOLS or upgrade the data plan.")
    g = pd.concat(frames)
    g["dv"] = g["close"] * g["volume"]
    agg = g.groupby("symbol").agg(dv=("dv", "mean"), price=("close", "last"))
    agg = agg[(agg.index.isin(listing)) & (agg["price"] >= ctx.s.min_price) & (agg["dv"] >= ctx.s.min_dollar_volume_swing)]
    top = agg.sort_values("dv", ascending=False).head(env.max_candidates)
    out = []
    for sym in top.index:
        try:
            out.append(ctx.hub.reference.ticker_info(sym))
        except DataUnavailable as e:
            excluded.append({"symbol": sym, "reasons": [f"Reference data unavailable: {e}"]})
    return out


def _intraday_bars(ctx: ScanContext, sym: str):
    day = current_or_last_session(ctx.now)
    bars = ctx.hub.market.intraday_bars(sym, day, 1, extended=True)
    if bars is None or bars.empty:
        return bars
    return bars[bars.index.time < time(16, 0)]  # evaluate the regular session, not after-hours prints


def analyse_symbol(ctx: ScanContext, info: TickerInfo, mkt: dict, events_all: list, earnings_known: bool,
                   spy1: pd.DataFrame | None) -> list[dict]:
    hub, s, sym = ctx.hub, ctx.s, info.symbol
    f = ctx.daily(sym)
    if f is None or len(f) < 30:
        raise DataUnavailable("insufficient daily history")
    try:
        q = hub.market.quote(sym)
    except DataUnavailable as e:
        q = None
        ctx.errors.append({"symbol": sym, "stage": "quote", "error": str(e)})
    price = q.price if q else float(f["close"].iloc[-1])
    price_time = q.timestamp if q else f.index[-1].to_pydatetime()
    fresh = freshness(price_time, hub.market.delay_minutes, synthetic=hub.synthetic, now=ctx.now)
    # News
    raw_news = []
    for p in hub.news:
        try:
            raw_news += p.company_news(sym, ctx.now - timedelta(days=14), 15)
        except DataUnavailable as e:
            ctx.errors.append({"symbol": sym, "stage": f"news ({p.name})", "error": str(e)})
    seen, uniq = set(), []
    for n in sorted(raw_news, key=lambda n: n.published_at, reverse=True):
        key = (n.headline.strip().lower()[:80])
        if key not in seen:
            seen.add(key)
            uniq.append(n)
    atr_pct = float(f["atr14"].iloc[-1] / f["close"].iloc[-1] * 100) if not pd.isna(f["atr14"].iloc[-1]) else None
    analysed = [news_mod.analyse(n, sym, f, atr_pct) for n in uniq[:12]]
    if hub.llm is not None and analysed:
        analysed = hub.llm.interpret(sym, analysed)
    n_score, n_notes = news_mod.news_score(analysed)
    verified_cat = news_mod.has_verified_catalyst(analysed, ctx.now - timedelta(days=5))
    promo = sum(1 for a in analysed if a["verification"] == "promotional")
    neg_major = any(a["sentiment"] == "negative" and a["impact"] == "high" and a["verification"] in
                    ("verified", "established") for a in analysed)
    # Filings, splits, social
    filings = None
    if hub.filings:
        try:
            filings = hub.filings.recent_filings(sym, ctx.today - timedelta(days=180))
        except DataUnavailable as e:
            ctx.errors.append({"symbol": sym, "stage": "SEC filings", "error": str(e)})
    splits = None
    if hasattr(hub.reference, "reverse_splits"):
        try:
            splits = hub.reference.reverse_splits(sym, ctx.today - timedelta(days=365))
        except DataUnavailable:
            splits = None
    social = hub.sentiment.social(sym) if hub.sentiment else None
    manip = manipulation.assess(f, info, verified_catalyst=verified_cat, promotional_news=promo, social=social,
                                filings=filings, reverse_splits=splits, spread_pct=q.spread_pct if q else None,
                                negative_major_news=neg_major)
    sym_events = [e for e in events_all if e.symbol in (sym, None)]
    decline = ev_mod.event_driven_decline(f, sym_events, analysed, sym)
    soc_score, soc_note = scoring.social_score(social, manip["flags"])
    sector_df = ctx.daily(info.sector_etf) if info.sector_etf else None
    sec_ok, sec_note = market.sector_aligned(info.sector, mkt["sectors"])
    regime = mkt["regime"]["regime"]

    cards = []
    for style in s.trading_styles:
        if style == "swing":
            a = analyze_swing(f, sym, bench=ctx.daily("SPY"), sector=sector_df)
        else:
            try:
                bars = _intraday_bars(ctx, sym)
            except DataUnavailable as e:
                ctx.errors.append({"symbol": sym, "stage": "intraday bars", "error": str(e)})
                continue
            prior_daily = f[f.index.date < current_or_last_session(ctx.now)]
            a = analyze_intraday(bars, sym, prior_daily, or_minutes=s.opening_range_minutes, spy1=spy1)
        liq = liquidity_eligibility(price, a.metrics.get("dollar_volume_20d") or float(f["dollar_vol20"].iloc[-1]),
                                    style, s, q.spread_pct if q else None, atr_pct)
        a.metrics.setdefault("dollar_volume_20d", float(f["dollar_vol20"].iloc[-1]))
        evinfo = ev_mod.event_risk(sym, style, ctx.today, sym_events, s, earnings_known)
        comp = scoring.components(a, news_score=n_score, regime=regime, sector_ok=sec_ok, social_score=soc_score,
                                  spread_pct=q.spread_pct if q else None)
        decl = decline if style == "swing" else None
        ev = scoring.evaluate(a, comp, s=s, regime=regime, manipulation=manip, liquidity_reasons=liq,
                              event_info=evinfo, event_decline=decl, earnings_known=earnings_known)
        expl = scoring.explain(a, ev, news_notes=n_notes, event_info=evinfo, manipulation=manip, regime=regime,
                               sector_note=sec_note, social_note=soc_note)
        sizing = None
        if a.plan:
            sizing = risk.position_size(equity=s.account_equity, risk_pct=s.risk_pct_per_trade, entry=a.plan.entry_price,
                                        stop=a.plan.stop, slippage_per_share=s.slippage_per_share,
                                        commission_per_trade=s.commission_per_trade, max_position_pct=s.max_position_pct,
                                        targets=[a.plan.target1, a.plan.target2], min_reward_risk=s.min_reward_risk)
        session_note = None
        if style == "intraday" and ctx.session != "open":
            session_note = (f"Regular session not open — intraday levels are from the "
                            f"{current_or_last_session(ctx.now)} session and are not live.")
        cards.append({
            "symbol": sym, "name": info.name, "exchange": info.exchange, "sector": info.sector,
            "industry": info.industry, "market_cap": info.market_cap, "cap_category": cap_category(info.market_cap, s),
            "style": style, "status": ev["status"], "scanner_status": a.status, "setup_type": a.setup_type,
            "technical_state": a.technical_state, "is_setup": a.is_setup, "qualifies": ev["qualifies"] and a.is_setup,
            "price": price, "price_time": price_time.isoformat(), "freshness": fresh.to_dict(),
            "bar_as_of": a.as_of, "session_note": session_note,
            "plan": a.plan.to_dict() if a.plan else None, "levels": [lv.to_dict() for lv in a.levels],
            "signals": [x.to_dict() for x in a.signals], "metrics": a.to_dict()["metrics"],
            "score": ev, "explanation": expl, "manipulation": manip, "news": analysed, "news_score": n_score,
            "events": evinfo, "event_driven_decline": decl, "position_size": sizing,
            "warnings": a.warnings + ([f"Reward/risk below {s.min_reward_risk:.1f}:1"]
                                      if a.plan and (a.plan.reward_risk or 0) < s.min_reward_risk else []),
            "reasons_avoid": a.reasons_avoid + ev["exclusions"] + ([ev["gate_reason"]] if ev["gate_reason"] else []),
            "social": ({"mentions_today": social.mentions_today, "mentions_avg": social.mentions_avg,
                        "positive_share": social.positive_share, "source": social.source} if social else None),
            "sources": {"prices": hub.market.name, "reference": info.source,
                        "news": sorted({n["source"] for n in analysed}), "filings": hub.filings.name if hub.filings else None,
                        "synthetic": hub.synthetic},
        })
    return cards


def run_scan(hub: DataHub, settings: ScanSettings, env: EnvSettings = ENV, now: datetime | None = None) -> dict:
    started = _time.time()
    if not hub.ready:
        return {"ok": False, "setup_required": True, "messages": hub.setup_messages, "status": hub.status()}
    ctx = ScanContext(hub, settings, now)
    try:
        infos, excluded = select_universe(ctx, env)
    except (DataUnavailable, NotConfigured) as e:
        return {"ok": False, "setup_required": isinstance(e, NotConfigured), "messages": [str(e)],
                "status": hub.status()}
    for i in infos:  # warm daily cache for breadth before the overview
        ctx.daily(i.symbol)
    frames = {i.symbol: ctx.daily_cache.get(i.symbol) for i in infos}
    mkt = market_overview(ctx, {k: v for k, v in frames.items() if v is not None})
    earnings_known = hub.events is not None
    events_all = []
    if hub.events:
        try:
            events_all = hub.events.earnings([i.symbol for i in infos], ctx.today - timedelta(days=20),
                                             ctx.today + timedelta(days=30))
        except DataUnavailable as e:
            earnings_known = False
            ctx.errors.append({"symbol": None, "stage": "earnings calendar", "error": str(e)})
    events_all += ev_mod.load_fomc()
    if hub.events:
        try:
            events_all += hub.events.economic(ctx.today, ctx.today + timedelta(days=14))
        except DataUnavailable:
            pass
    spy1 = None
    if "intraday" in settings.trading_styles:
        try:
            spy1 = _intraday_bars(ctx, "SPY")
        except DataUnavailable as e:
            ctx.errors.append({"symbol": "SPY", "stage": "intraday bars", "error": str(e)})
    cards: list[dict] = []

    def work(info):
        try:
            return analyse_symbol(ctx, info, mkt, events_all, earnings_known, spy1)
        except DataUnavailable as e:
            ctx.errors.append({"symbol": info.symbol, "stage": "analysis", "error": str(e)})
        except Exception as e:  # noqa: BLE001 — one bad symbol must not stop the scan
            log.error("analysis failed for %s: %s", info.symbol, traceback.format_exc())
            ctx.errors.append({"symbol": info.symbol, "stage": "analysis", "error": f"{type(e).__name__}: {e}"})
        return []

    with ThreadPoolExecutor(max_workers=1 if hub.synthetic else 6) as pool:
        for res in pool.map(work, infos):
            cards += res
    out = {"ok": True, "mode": hub.mode, "synthetic": hub.synthetic, "generated_at": datetime.now(ET).isoformat(),
           "market_time": ctx.now.isoformat(), "session": ctx.session, "market": mkt,
           "setup_messages": hub.setup_messages, "errors": ctx.errors, "excluded": excluded,
           "universe_size": len(infos), "duration_s": round(_time.time() - started, 2),
           "probability_note": ("Scores rate setup quality on documented rules. They are not probabilities of "
                                "profit; no calibrated probability model is in use.")}
    for style in [x for x in ("intraday", "swing") if x in settings.trading_styles]:
        sc = [c for c in cards if c["style"] == style]
        flagged = [c for c in sc if c["manipulation"].get("level") in ("high", "elevated")]
        flagged_syms = {c["symbol"] for c in flagged if c["manipulation"]["level"] == "high"}
        ranked = sorted([c for c in sc if c["qualifies"] and c["symbol"] not in flagged_syms],
                        key=lambda c: -c["score"]["score"])
        for i, c in enumerate(ranked):
            c["rank"] = i + 1
        scoring.ranking_notes(ranked)
        out[style] = {
            "ranked": ranked,
            "watch_only": sorted([c for c in sc if c["is_setup"] and not c["qualifies"] and
                                  c["status"] in ("Watch", "Approaching Entry", "Triggered") and
                                  c["symbol"] not in flagged_syms], key=lambda c: -c["score"]["score"]),
            "avoid": [c for c in sc if c["status"] in ("Avoid", "Invalidated") and c["is_setup"]
                      and c["symbol"] not in flagged_syms],
            "flagged": sorted(flagged, key=lambda c: -(c["manipulation"]["score"] or 0)),
            "all": {c["symbol"]: c for c in sc},
            "message": None if ranked else NO_SETUPS,
        }
    return jsonable(out)


def analyse_single(hub: DataHub, settings: ScanSettings, symbol: str, env: EnvSettings = ENV) -> dict:
    """Research view for one symbol, including ones outside the universe (with the exclusion reasons)."""
    if not hub.ready:
        return {"ok": False, "setup_required": True, "messages": hub.setup_messages}
    ctx = ScanContext(hub, settings)
    try:
        info = hub.reference.ticker_info(symbol)
    except DataUnavailable as e:
        return {"ok": False, "error": str(e)}
    mkt = market_overview(ctx, {})
    events_all = []
    earnings_known = hub.events is not None
    if hub.events:
        try:
            events_all = hub.events.earnings([symbol], ctx.today - timedelta(days=20), ctx.today + timedelta(days=45))
        except DataUnavailable:
            earnings_known = False
    events_all += ev_mod.load_fomc()
    spy1 = None
    try:
        spy1 = _intraday_bars(ctx, "SPY")
    except DataUnavailable:
        pass
    try:
        cards = analyse_symbol(ctx, info, mkt, events_all, earnings_known, spy1)
    except DataUnavailable as e:
        return {"ok": False, "error": str(e)}
    universe_reasons = security_eligibility(info, settings)
    for c in cards:
        if universe_reasons:
            c["status"], c["qualifies"] = "Avoid", False
            c["reasons_avoid"] = universe_reasons + c["reasons_avoid"]
    return jsonable({"ok": True, "synthetic": hub.synthetic, "info": info.to_dict(),
                     "universe_reasons": universe_reasons, "cards": {c["style"]: c for c in cards},
                     "regime": mkt["regime"], "errors": ctx.errors, "session": ctx.session,
                     "market_time": ctx.now.isoformat()})


def chart_data(hub: DataHub, symbol: str, now: datetime | None = None) -> dict:
    now = (now or (hub.market.now() if hub.synthetic else now_et())).astimezone(ET)
    today = now.date()
    raw = hub.market.daily_bars(symbol, today - timedelta(days=HISTORY_DAYS), today)
    f = ind.add_daily_indicators(raw) if len(raw) else None
    out = {"symbol": symbol, "synthetic": hub.synthetic, "daily": [], "intraday": [], "intraday_session": None}
    if f is not None:
        f = f.iloc[-260:]
        for ts, r in f.iterrows():
            out["daily"].append({k: (None if pd.isna(v) else round(float(v), 4)) for k, v in
                                 {"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                                  "volume": r["volume"], "sma20": r["sma20"], "sma50": r["sma50"],
                                  "sma200": r["sma200"], "rsi": r["rsi14"], "macd": r["macd"],
                                  "macd_signal": r["macd_signal"], "macd_hist": r["macd_hist"]}.items()}
                                | {"time": ts.date().isoformat()})
        out["daily_partial_last_bar"] = bool(raw.attrs.get("partial_last_bar"))
    day = current_or_last_session(now)
    try:
        b1 = hub.market.intraday_bars(symbol, day, 1, extended=True)
    except DataUnavailable:
        b1 = None
    if b1 is not None and not b1.empty:
        reg = b1[(b1.index.time >= time(9, 30)) & (b1.index.time < time(16, 0))]
        b5 = ind.resample(reg, 5) if not reg.empty else reg
        vw = ind.vwap(reg) if not reg.empty else None
        for ts, r in b5.iterrows():
            v = float(vw[vw.index < ts + pd.Timedelta(minutes=5)].iloc[-1]) if vw is not None else None
            # Chart libraries render epoch seconds as UTC, so encode the ET wall-clock time.
            out["intraday"].append({"time": int(ts.tz_localize(None).tz_localize("UTC").timestamp()), "open": float(r["open"]), "high": float(r["high"]),
                                    "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"]),
                                    "vwap": round(v, 4) if v else None})
        out["intraday_session"] = day.isoformat()
    return jsonable(out)
