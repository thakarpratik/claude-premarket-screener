from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from pullback_radar.app import create_app
from pullback_radar.config import EnvSettings
from pullback_radar.market_calendar import ET
from pullback_radar.providers import build_hub

H = {"X-Requested-With": "pullback-radar"}


@pytest.fixture
def client(tmp_path):
    env = EnvSettings(data_mode="demo", upload_dir=tmp_path)
    hub = build_hub(env, demo_now=datetime(2026, 10, 8, 18, 0, tzinfo=ET))
    with TestClient(create_app(env, hub, "sqlite://", start_scheduler=False)) as c:
        r = c.post("/api/auth/register", json={"email": "t@example.com", "password": "correct-horse-1"}, headers=H)
        assert r.status_code == 200
        yield c


def test_auth_required_and_csrf(client):
    assert client.post("/api/scan").status_code == 403  # cookie without the custom header
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/scan/latest").status_code == 401
    assert client.post("/api/auth/login", json={"email": "t@example.com", "password": "wrong"}, headers=H).status_code == 401
    assert client.post("/api/auth/login", json={"email": "t@example.com", "password": "correct-horse-1"},
                       headers=H).status_code == 200


def test_registration_validation(client):
    r = client.post("/api/auth/register", json={"email": "bad", "password": "short"}, headers=H)
    assert r.status_code == 422


def test_scan_and_feeds(client):
    s = client.post("/api/scan", headers=H).json()
    assert s["ok"] and s["synthetic"] and s["market"]["regime"]["regime"] in ("bullish", "mixed", "bearish")
    sw = client.get("/api/opportunities/swing").json()
    ranked = sw["ranked"]
    assert ranked and all(c["qualifies"] for c in ranked)
    assert [c["score"]["score"] for c in ranked] == sorted([c["score"]["score"] for c in ranked], reverse=True)
    flagged = {f["symbol"] for f in client.get("/api/flagged").json()["flagged"]}
    assert "DEMO06" in flagged and not flagged & {c["symbol"] for c in ranked}  # never mixed into the feed
    excluded = {e["symbol"] for e in client.get("/api/excluded").json()["excluded"]}
    assert {"DEMODX", "DEMOOT", "DEMO10"} <= excluded
    avoid = {c["symbol"]: c for c in sw["avoid"]}
    assert "DEMO08" in avoid and any("blackout" in r for r in avoid["DEMO08"]["reasons_avoid"])
    assert "DEMO09" in avoid  # illiquid
    for c in ranked:
        assert c["price_time"] and c["freshness"]["status"] == "synthetic"
        p = c["plan"]
        assert p["stop"] < p["entry_price"] < p["target1"]


def test_research_and_watchlist_and_paper(client):
    client.post("/api/scan", headers=H)
    r = client.get("/api/stocks/DEMO01").json()
    assert r["cards"]["swing"]["plan"] and len(r["chart"]["daily"]) > 100
    etf = client.get("/api/stocks/DEMODX").json()
    assert etf["universe_reasons"] and etf["cards"]["swing"]["status"] == "Avoid"
    w = client.post("/api/watchlist", json={"symbol": "DEMO01", "style": "swing"}, headers=H).json()
    assert w["plan_snapshot"]["stop"]
    assert client.post("/api/watchlist", json={"symbol": "bad sym!"}, headers=H).status_code == 422
    paper = client.post("/api/paper/from-setup", json={"symbol": "DEMO01", "style": "swing"}, headers=H)
    assert paper.status_code == 200 and "No order was sent" in paper.json()["note"]
    refused = client.post("/api/paper/from-setup", json={"symbol": "DEMO13", "style": "swing"}, headers=H)
    assert refused.status_code == 409  # not triggered yet
    stats = client.get("/api/trades").json()["stats"]
    assert stats["paper"]["open_positions"] == 1


def test_settings_roundtrip_and_validation(client):
    assert client.put("/api/settings", json={"small_cap_min": 5e9}, headers=H).status_code == 422
    r = client.put("/api/settings", json={"min_reward_risk": 3, "trading_styles": ["swing"]}, headers=H).json()
    assert r["settings"]["min_reward_risk"] == 3
    assert client.get("/api/opportunities/intraday").json().get("disabled")


def test_trade_journal_crud(client):
    t = client.post("/api/trades", headers=H, json={"mode": "actual", "symbol": "abc", "style": "swing",
                                                    "entry_time": "2026-10-01T14:00:00Z", "entry_price": 10,
                                                    "quantity": 100, "stop": 9.5}).json()
    assert t["symbol"] == "ABC" and t["status"] == "open"
    t2 = client.patch(f"/api/trades/{t['id']}", headers=H, json={"exit_price": 11}).json()
    assert t2["status"] == "closed" and t2["pnl"] == 100 and t2["r_multiple"] == 2
    bad = client.post("/api/trades", headers=H, json={"mode": "actual", "symbol": "x", "style": "swing",
                                                      "entry_time": "2026-10-01T14:00:00Z", "entry_price": 10,
                                                      "quantity": 1, "stop": 11})
    assert bad.status_code == 422
    up = client.post(f"/api/trades/{t['id']}/screenshot", headers=H, files={"file": ("a.txt", b"x", "text/plain")})
    assert up.status_code == 415
    ok = client.post(f"/api/trades/{t['id']}/screenshot", headers=H, files={"file": ("a.png", b"\x89PNG", "image/png")})
    assert ok.status_code == 200 and client.get(f"/api/trades/{t['id']}/screenshot").status_code == 200


def test_risk_endpoint(client):
    r = client.post("/api/risk/position-size", headers=H, json={"equity": 10000, "entry": 20, "stop": 19, "targets": [23]}).json()
    assert r["shares"] == 50 and r["targets"][0]["reward_risk"] == 3


def test_live_mode_without_keys_shows_setup_message(tmp_path):
    env = EnvSettings(data_mode="live")
    with TestClient(create_app(env, None, "sqlite://", start_scheduler=False)) as c:
        c.post("/api/auth/register", json={"email": "z@example.com", "password": "correct-horse-1"}, headers=H)
        st = c.get("/api/status").json()
        assert not st["ready"] and not st["synthetic"]
        r = c.get("/api/scan/latest").json()
        assert r["ok"] is False and r["setup_required"] and any("POLYGON_API_KEY" in m for m in r["messages"])
