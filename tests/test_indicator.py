"""Unit tests for Pine indicator mathematical definitions and triggers."""
import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from app.strategy.indicator import (
    calculate_sma,
    calculate_ema,
    calculate_rma,
    calculate_atr,
    calculate_rsi,
    calculate_vwap,
    round_to_mintick,
    PineIndicatorState
)

IST = ZoneInfo("Asia/Kolkata")

def test_round_to_mintick():
    assert round_to_mintick(100.03, 0.05) == 100.05
    assert round_to_mintick(100.02, 0.05) == 100.00
    assert round_to_mintick(152.47, 0.05) == 152.45
    assert round_to_mintick(152.48, 0.05) == 152.50

def test_sma():
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    sma = calculate_sma(data, 3)
    assert sma[0] is None
    assert sma[1] is None
    assert sma[2] == 20.0  # (10+20+30)/3
    assert sma[3] == 30.0  # (20+30+40)/3
    assert sma[4] == 40.0  # (30+40+50)/3

def test_ema():
    # EMA length 3: alpha = 2/(3+1) = 0.5
    data = [10.0, 20.0, 30.0, 40.0]
    ema = calculate_ema(data, 3)
    assert ema[0] is None
    assert ema[1] is None
    assert ema[2] == 20.0  # SMA seed (10+20+30)/3
    # next: 0.5 * 40.0 + 0.5 * 20.0 = 30.0
    assert ema[3] == 30.0

def test_rma():
    # Wilder RMA length 3: alpha = 1/3
    data = [10.0, 20.0, 30.0, 60.0]
    rma = calculate_rma(data, 3)
    assert rma[0] is None
    assert rma[1] is None
    assert rma[2] == 20.0  # SMA seed
    # next: (1/3)*60 + (2/3)*20 = 20 + 13.3333 = 33.3333
    assert pytest.approx(rma[3], 0.001) == 33.3333

def test_rsi_range():
    # Constant ascending prices should produce high RSI
    closes = [float(100 + i * 2) for i in range(30)]
    rsi = calculate_rsi(closes, 14)
    valid_rsi = [r for r in rsi if r is not None]
    assert len(valid_rsi) > 0
    assert all(r > 70.0 for r in valid_rsi)

def test_indicator_state_evaluation():
    # Build 30 synthetic uptrend bars during market hours
    start_dt = datetime(2026, 10, 8, 10, 0, tzinfo=IST)
    candles = []
    base_price = 100.0

    for i in range(35):
        dt = start_dt + timedelta(minutes=5 * i)
        o = base_price + i * 1.5
        c = o + 2.0
        h = c + 0.5
        l = o - 0.2
        v = 1000.0 + i * 50
        candles.append({
            "timestamp": dt,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v
        })

    state = PineIndicatorState(mintick=0.05)
    res = state.evaluate(
        candles=candles,
        symbol="NIFTY26OCT25000CE",
        strict=False,
        useIndex=False,
        useVWAP=False,
        useVolume=False
    )

    assert res["ready"] is True
    assert res["ema9"] is not None
    assert res["ema21"] is not None
    assert res["atr"] is not None
    assert res["rsi"] is not None
    assert res["candidateStop"] < res["close"]
    assert res["target"] > res["close"]
