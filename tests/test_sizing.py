"""Unit tests for position sizing and risk caps."""
import pytest
from app.config import settings
from app.execution.sizing import PositionSizer

def test_base_sizing():
    sizer = PositionSizer()
    # ₹100,000 capital, 15% base = ₹15,000 budget
    # Option premium ₹100, lot size 65 -> cost per lot = ₹6,500
    # lots = floor(15000 / 6500) = 2 lots (130 qty)
    res = sizer.evaluate_entry(
        available_capital=100000.0,
        entry_premium=100.0,
        stop_loss=85.0,  # risk per share = 15
        lot_size=65,
        closed_trades=[],
        today_pnl=0.0,
        today_trade_count=0
    )
    assert res["allowed"] is True
    assert res["lots"] == 2
    assert res["quantity"] == 130
    assert res["allocation_pct"] == 15.0

def test_budget_cannot_afford_one_lot():
    sizer = PositionSizer()
    # ₹10,000 capital, 15% = ₹1,500 budget
    # Option premium ₹100, lot size 65 -> cost per lot = ₹6,500
    # Budget < ₹6,500 -> should skip trade
    res = sizer.evaluate_entry(
        available_capital=10000.0,
        entry_premium=100.0,
        stop_loss=85.0,
        lot_size=65,
        closed_trades=[],
        today_pnl=0.0,
        today_trade_count=0
    )
    assert res["allowed"] is False
    assert res["lots"] == 0
    assert "cannot afford 1 lot" in res["reason"]

def test_losing_streak_adaptive_reduction():
    sizer = PositionSizer()
    # 3 consecutive losing trades -> should reduce allocation from 15% to 9%
    trades = [
        {"net_pnl": -500.0},
        {"net_pnl": -800.0},
        {"net_pnl": -350.0}
    ]
    pct, reason = sizer.calculate_allocation_pct(
        entry_premium=100.0,
        stop_loss=90.0,
        closed_trades=trades,
        today_pnl=0.0
    )
    assert pct == 9.0  # 15 - 6
    assert "Losing streak of 3" in reason

def test_max_daily_loss_blocker():
    sizer = PositionSizer()
    # If today's pnl breached MAX_DAILY_LOSS (default ₹5,000)
    res = sizer.evaluate_entry(
        available_capital=100000.0,
        entry_premium=100.0,
        stop_loss=90.0,
        lot_size=65,
        closed_trades=[],
        today_pnl=-5500.0,  # Breached
        today_trade_count=2
    )
    assert res["allowed"] is False
    assert "Max daily loss" in res["reason"]

def test_kill_switch_blocker():
    sizer = PositionSizer()
    res = sizer.evaluate_entry(
        available_capital=100000.0,
        entry_premium=100.0,
        stop_loss=90.0,
        lot_size=65,
        closed_trades=[],
        today_pnl=0.0,
        today_trade_count=0,
        is_kill_switch_active=True
    )
    assert res["allowed"] is False
    assert "kill-switch" in res["reason"].lower()
