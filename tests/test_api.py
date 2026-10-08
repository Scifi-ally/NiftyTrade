"""Integration tests for FastAPI endpoints, safety guards, and mode switches."""
import pytest
from fastapi.testclient import TestClient
from app.web.server import app, system_state
from app.data.candle_builder import CandleBuilder
from app.strategy.state_machine import StrategyStateMachine

client = TestClient(app)

def setup_module():
    """Ensure state machine is instantiated for tests."""
    if not system_state.candle_builder:
        system_state.candle_builder = CandleBuilder(timeframe_minutes=5)
    if not system_state.state_machine:
        system_state.state_machine = StrategyStateMachine(candle_builder=system_state.candle_builder)

def test_index_page():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "NiftyTrades" in resp.text
    assert "Lightweight Charts" in resp.text or "chart-container" in resp.text

def test_api_status():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_mode"] == "PAPER"
    assert "funds" in data
    assert "today_pnl" in data

def test_mode_switch_guard():
    # Attempting to switch to LIVE without typing "LIVE" must be rejected
    resp = client.post("/api/mode", json={"mode": "LIVE", "confirmation": ""})
    assert resp.status_code == 400
    assert "You must type 'LIVE'" in resp.json()["detail"]

    resp_wrong = client.post("/api/mode", json={"mode": "LIVE", "confirmation": "yes"})
    assert resp_wrong.status_code == 400
    assert "You must type 'LIVE'" in resp_wrong.json()["detail"]

def test_kill_switch_toggle():
    resp = client.post("/api/kill-switch")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "SUCCESS"
    assert data["is_kill_switch_active"] is True

    # Toggle back to False
    resp2 = client.post("/api/kill-switch")
    assert resp2.status_code == 200
    assert resp2.json()["is_kill_switch_active"] is False

def test_candles_endpoint():
    resp = client.get("/api/candles")
    assert resp.status_code == 200
    assert "candles" in resp.json()

def test_trades_endpoint():
    resp = client.get("/api/trades")
    assert resp.status_code == 200
    assert "trades" in resp.json()
