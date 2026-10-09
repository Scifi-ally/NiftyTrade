"""Unit tests for Paper Trading Engine verifying realistic pricing, slippage, and Indian statutory charges."""
import pytest
from app.execution.paper_engine import PaperTradingEngine, calculate_indian_option_charges
from app.config import settings
from app.storage.db import Database
import tempfile
from pathlib import Path

@pytest.fixture
def temp_paper_engine():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        test_db_path = Path(tmpdir) / "test_paper.db"
        test_db = Database(test_db_path)
        
        # Monkeypatch db in paper_engine
        import app.execution.paper_engine as pe_mod
        orig_db = pe_mod.db
        pe_mod.db = test_db
        
        engine = PaperTradingEngine()
        yield engine
        
        pe_mod.db = orig_db

def test_indian_option_charges_calculation():
    # Buy 65 qty (1 lot NIFTY) @ 100.0, Sell @ 120.0
    charges = calculate_indian_option_charges(buy_price=100.0, sell_price=120.0, quantity=65)
    
    buy_turnover = 100.0 * 65  # 6500.0
    sell_turnover = 120.0 * 65 # 7800.0
    total_turnover = 14300.0
    
    assert charges["brokerage"] == 40.0 # 20 entry + 20 exit
    assert charges["stt"] == round(sell_turnover * 0.001, 2) # 7.80
    assert charges["exchange_charges"] == round(total_turnover * 0.0005, 2) # 7.15
    assert charges["sebi_charges"] == round(total_turnover * 0.000001, 2) # 0.01
    assert charges["stamp_duty"] == round(buy_turnover * 0.00003, 2) # 0.20
    # GST on (40 + 7.15 + 0.01) * 0.18 = 8.49
    assert charges["gst"] == round((40.0 + 7.15 + 0.01) * 0.18, 2)
    assert charges["total_charges"] > 60.0 # Approx Rs. 63.65

def test_entry_enforces_slippage_when_depth_missing(temp_paper_engine):
    tick = {"token": "44596", "ltp": 150.0, "best_ask": 0.0, "latency_ms": 2.0}
    trade = temp_paper_engine.execute_entry(
        token="44596",
        symbol="NIFTY_150CE",
        option_type="CE",
        quantity=65,
        tick=tick,
        stop_loss=135.0,
        target=180.0
    )
    # Must NOT fill at 150.0; must pay LTP + SLIPPAGE_POINTS (150.50)
    assert trade["entry_price"] == 150.50
    assert trade["status"] == "OPEN"

def test_entry_uses_real_ask_when_available(temp_paper_engine):
    tick = {"token": "44596", "ltp": 150.0, "best_ask": 150.80, "latency_ms": 2.0}
    trade = temp_paper_engine.execute_entry(
        token="44596",
        symbol="NIFTY_150CE",
        option_type="CE",
        quantity=65,
        tick=tick,
        stop_loss=135.0,
        target=180.0
    )
    assert trade["entry_price"] == 150.80

def test_exit_enforces_slippage_and_full_charges(temp_paper_engine):
    # Enter at 100
    temp_paper_engine.execute_entry(
        token="44596",
        symbol="NIFTY_100CE",
        option_type="CE",
        quantity=65,
        tick={"token": "44596", "ltp": 100.0, "best_ask": 0.0},
        stop_loss=80.0,
        target=140.0
    )
    # Entry price will be 100.50 due to slippage
    assert temp_paper_engine.active_trade["entry_price"] == 100.50

    # Exit when LTP is 120.0, depth missing
    closed = temp_paper_engine.execute_exit(
        tick={"token": "44596", "ltp": 120.0, "best_bid": 0.0},
        reason="TARGET TOUCHED"
    )
    # Exit fill price must be 120 - 0.50 = 119.50 (NOT 120.0)
    assert closed["exit_price"] == 119.50
    
    # Gross PnL: (119.50 - 100.50) * 65 = 19.0 * 65 = 1235.0
    assert closed["gross_pnl"] == 1235.0
    # Net PnL must be strictly less than Gross PnL due to all statutory taxes and brokerage
    assert closed["charges"] > 60.0
    assert closed["net_pnl"] == round(1235.0 - closed["charges"], 2)
    assert closed["net_pnl"] < closed["gross_pnl"]
    assert temp_paper_engine.has_open_position is False

def test_stop_loss_exit_shows_honest_loss(temp_paper_engine):
    temp_paper_engine.execute_entry(
        token="44596",
        symbol="NIFTY_100CE",
        option_type="CE",
        quantity=65,
        tick={"token": "44596", "ltp": 100.0, "best_ask": 0.0},
        stop_loss=85.0,
        target=130.0
    )
    # Exit on SL trigger at 85.0
    closed = temp_paper_engine.execute_exit(
        tick={"token": "44596", "ltp": 85.0, "best_bid": 0.0},
        reason="SL TOUCHED"
    )
    # Fills at 85 - 0.50 = 84.50
    assert closed["exit_price"] == 84.50
    # Loss: (84.50 - 100.50) * 65 = -16.0 * 65 = -1040.0
    assert closed["gross_pnl"] == -1040.0
    # Net loss is worse than gross loss due to brokerage and taxes
    assert closed["net_pnl"] < -1040.0
