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

def test_index_direction_confirmation():
    start_dt = datetime(2026, 10, 8, 10, 0, tzinfo=IST)
    opt_candles = []
    base_price = 100.0
    for i in range(35):
        dt = start_dt + timedelta(minutes=5 * i)
        o = base_price + i * 1.5
        c = o + 2.0
        h = c + 0.5
        l = o - 0.2
        v = 1000.0 + i * 50
        opt_candles.append({
            "timestamp": dt, "open": o, "high": h, "low": l, "close": c, "volume": v
        })

    # Index candles with strong uptrend
    idx_uptrend = []
    for i in range(35):
        dt = start_dt + timedelta(minutes=5 * i)
        idx_uptrend.append({
            "timestamp": dt, "open": 25000.0 + i * 10, "high": 25015.0 + i * 10,
            "low": 24995.0 + i * 10, "close": 25010.0 + i * 10, "volume": 10000
        })

    # Index candles with strong downtrend
    idx_downtrend = []
    for i in range(35):
        dt = start_dt + timedelta(minutes=5 * i)
        idx_downtrend.append({
            "timestamp": dt, "open": 25500.0 - i * 10, "high": 25505.0 - i * 10,
            "low": 25480.0 - i * 10, "close": 25485.0 - i * 10, "volume": 10000
        })

    state = PineIndicatorState(mintick=0.05)

    # 1. CE option (+1) with Index uptrend (+1) -> buySetup allowed (or indexOK True)
    res_ce_ok = state.evaluate(
        candles=opt_candles,
        symbol="NIFTY26OCT25000CE",
        contract_side=1,
        useIndex=True,
        index_candles=idx_uptrend,
        useVWAP=False,
        useVolume=False
    )
    assert res_ce_ok["ready"] is True
    # Verify index direction was detected as +1
    # 2. CE option (+1) with Index downtrend (-1) -> must be rejected by index filter
    res_ce_blocked = state.evaluate(
        candles=opt_candles,
        symbol="NIFTY26OCT25000CE",
        contract_side=1,
        useIndex=True,
        index_candles=idx_downtrend,
        useVWAP=False,
        useVolume=False
    )
    assert res_ce_blocked["buySetup"] is False

    # 3. PE option (-1) with Index downtrend (-1) -> indexOK is satisfied
    res_pe_ok = state.evaluate(
        candles=opt_candles,
        symbol="NIFTY26OCT25000PE",
        contract_side=-1,
        useIndex=True,
        index_candles=idx_downtrend,
        useVWAP=False,
        useVolume=False
    )
    assert res_pe_ok["ready"] is True

def test_confirmed_trend_break_counter():
    state = PineIndicatorState()
    assert state.trend_break_count == 0

    # Build 30 baseline candles
    start_dt = datetime(2026, 10, 8, 10, 0, tzinfo=IST)
    candles = []
    for i in range(30):
        dt = start_dt + timedelta(minutes=i)
        candles.append({
            "timestamp": dt, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000.0
        })

    # Evaluate baseline
    res = state.evaluate(candles, trendExitBars=2)
    assert res["confirmedTrendBreak"] is False

    # Candle with severe breakdown below EMA9 and EMA21 (bar 1)
    candles.append({
        "timestamp": start_dt + timedelta(minutes=30),
        "open": 90.0, "high": 90.0, "low": 70.0, "close": 70.0, "volume": 1000.0
    })
    res1 = state.evaluate(candles, trendExitBars=2)
    assert state.trend_break_count == 1
    assert res1["confirmedTrendBreak"] is False

    # Candle 2 continuing breakdown below EMA21 and EMA9 (bar 2)
    candles.append({
        "timestamp": start_dt + timedelta(minutes=31),
        "open": 70.0, "high": 71.0, "low": 60.0, "close": 60.0, "volume": 1000.0
    })
    res2 = state.evaluate(candles, trendExitBars=2)
    assert state.trend_break_count == 2
    assert res2["confirmedTrendBreak"] is True

    # Candle bouncing back above EMA (resets counter)
    candles.append({
        "timestamp": start_dt + timedelta(minutes=32),
        "open": 60.0, "high": 150.0, "low": 60.0, "close": 150.0, "volume": 1000.0
    })
    res3 = state.evaluate(candles, trendExitBars=2)
    assert state.trend_break_count == 0
    assert res3["confirmedTrendBreak"] is False

