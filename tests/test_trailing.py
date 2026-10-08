"""Unit tests for trailing stop logic and breakeven adjustment."""
import pytest
from app.config import settings
from app.strategy.indicator import round_to_mintick

def simulate_trailing_stop(
    entry_price: float,
    initial_stop: float,
    entry_atr: float,
    price_sequence: list[float],
    atr_mult: float = 0.8
) -> list[float]:
    """Simulates trailing stop updates along a tick sequence."""
    current_stop = initial_stop
    highest_price = entry_price
    risk_1r = entry_price - initial_stop

    stops = [current_stop]

    for p in price_sequence:
        if p > highest_price:
            highest_price = p

        # Move to BE at +1R
        if p >= (entry_price + risk_1r):
            be_sl = entry_price
            trail_sl = round_to_mintick(highest_price - (atr_mult * entry_atr), 0.05)
            new_stop = max(be_sl, trail_sl)
            if new_stop > current_stop:
                current_stop = new_stop

        stops.append(current_stop)

    return stops

def test_trailing_stop_moves_only_upward():
    entry = 100.0
    initial_sl = 90.0  # 1R = 10 points
    atr = 5.0
    # Price rises to +1R (110.0), then higher (120.0), then pulls back (115.0)
    prices = [102.0, 105.0, 110.0, 115.0, 120.0, 115.0, 112.0]
    stops = simulate_trailing_stop(entry, initial_sl, atr, prices)

    # Before 110 (1R), stop remains initial_sl (90.0)
    assert stops[1] == 90.0
    assert stops[2] == 90.0

    # At 110 (1R), moves to BE (100.0) or highest - 0.8*5 = 110 - 4 = 106.0
    # max(100.0, 106.0) = 106.0
    assert stops[3] >= 100.0

    # At 120, trail_sl = 120 - 4 = 116.0
    assert stops[5] == 116.0

    # When price pulls back to 115 and 112, stop must NEVER move down
    assert stops[6] == 116.0
    assert stops[7] == 116.0
