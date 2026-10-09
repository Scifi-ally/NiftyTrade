"""Unit tests for ScripMaster resolution, CandleBuilder close_time, and PositionSizer safety checks."""
import pytest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from app.data.scrip_master import ScripMaster
from app.data.candle_builder import CandleBuilder, Candle
from app.execution.sizing import PositionSizer
from app.core.exceptions import SymbolRejectedError, ScripMasterError

IST = ZoneInfo("Asia/Kolkata")

def test_symbol_validation():
    master = ScripMaster()
    # Reject non-NIFTY
    with pytest.raises(SymbolRejectedError):
        master.validate_nifty_symbol("BANKNIFTY26OCT48000CE")
    with pytest.raises(SymbolRejectedError):
        master.validate_nifty_symbol("RELIANCE")
    with pytest.raises(SymbolRejectedError):
        master.validate_nifty_symbol("FINNIFTY")

    # Accept NIFTY
    assert master.validate_nifty_symbol("NIFTY 50") is True
    assert master.validate_nifty_symbol("NIFTY") is True
    assert master.validate_nifty_symbol("NIFTY26OCT25000CE") is True
    assert master.validate_nifty_symbol("NIFTY26OCT25000PE") is True

def test_resolve_atm_options_and_fixed_option():
    master = ScripMaster()
    exp1 = date(2026, 10, 15)
    exp2 = date(2026, 10, 22)
    master._nifty_options = [
        {
            "token": "1001",
            "symbol": "NIFTY15OCT25000CE",
            "name": "NIFTY",
            "expiry_str": "15OCT2026",
            "expiry_date": exp1,
            "strike": 25000.0,
            "option_type": "CE",
            "lot_size": 65,
            "tick_size": 0.05,
            "freeze_qty": 1800
        },
        {
            "token": "1002",
            "symbol": "NIFTY15OCT25000PE",
            "name": "NIFTY",
            "expiry_str": "15OCT2026",
            "expiry_date": exp1,
            "strike": 25000.0,
            "option_type": "PE",
            "lot_size": 65,
            "tick_size": 0.05,
            "freeze_qty": 1800
        },
        {
            "token": "1003",
            "symbol": "NIFTY22OCT25100CE",
            "name": "NIFTY",
            "expiry_str": "22OCT2026",
            "expiry_date": exp2,
            "strike": 25100.0,
            "option_type": "CE",
            "lot_size": 65,
            "tick_size": 0.05,
            "freeze_qty": 1800
        }
    ]
    master._is_loaded = True

    # Test ATM resolution for spot 25015 -> rounds to 25000
    ce, pe = master.resolve_atm_options(spot_price=25015.0, expiry=exp1)
    assert ce["strike"] == 25000.0
    assert ce["option_type"] == "CE"
    assert pe["strike"] == 25000.0
    assert pe["option_type"] == "PE"

    # Test get_fixed_option with exact strike and expiry
    fixed_ce = master.get_fixed_option(strike=25100.0, option_type="CE", expiry="22OCT2026")
    assert fixed_ce["token"] == "1003"
    assert fixed_ce["strike"] == 25100.0

    # Test invalid option type
    with pytest.raises(ScripMasterError):
        master.get_fixed_option(strike=25000.0, option_type="XYZ")

def test_candle_builder_close_time():
    builder = CandleBuilder(timeframe_minutes=5)
    t0 = datetime(2026, 10, 8, 9, 30, tzinfo=IST)
    c = Candle(
        timestamp=t0,
        open_price=100.0,
        high_price=105.0,
        low_price=99.0,
        close_price=104.0,
        volume=500.0,
        timeframe_minutes=5
    )
    # close_time should be 9:35
    assert c.close_time == datetime(2026, 10, 8, 9, 35, tzinfo=IST)

    # Historical load with candle list
    hist_raw = [
        ["2026-10-08T09:15:00+05:30", 25000.0, 25050.0, 24980.0, 25020.0, 1000],
        ["2026-10-08T09:20:00+05:30", 25020.0, 25080.0, 25010.0, 25070.0, 1200]
    ]
    builder.load_historical_candles("NIFTY 50", hist_raw)
    completed = builder.get_completed_candles("NIFTY 50")
    assert len(completed) == 2
    assert completed[0].close_time == datetime(2026, 10, 8, 9, 20, tzinfo=IST)

def test_position_sizer_stale_feed_and_spread():
    sizer = PositionSizer()

    # Stale feed should block entry
    res_stale = sizer.evaluate_entry(
        available_capital=100000.0,
        entry_premium=100.0,
        stop_loss=85.0,
        lot_size=65,
        closed_trades=[],
        today_pnl=0.0,
        today_trade_count=0,
        is_feed_stale=True
    )
    assert res_stale["allowed"] is False
    assert "feed is stale" in res_stale["reason"].lower()

    # Wide bid-ask spread (> 3%) should block entry
    res_wide = sizer.evaluate_entry(
        available_capital=100000.0,
        entry_premium=100.0,
        stop_loss=85.0,
        lot_size=65,
        closed_trades=[],
        today_pnl=0.0,
        today_trade_count=0,
        bid_ask_spread_pct=3.5
    )
    assert res_wide["allowed"] is False
    assert "spread" in res_wide["reason"].lower()
