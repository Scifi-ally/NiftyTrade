"""Deterministic adaptive position sizing and risk caps management."""
import math
from typing import Any, Dict, List, Optional, Tuple
from app.config import settings
from app.core.logging import logger

class PositionSizer:
    """
    Computes exact trade quantity and applies risk caps.
    Decides only HOW MUCH to buy after indicator has given BUY signal.
    Never alters or creates entry signals.
    """

    def __init__(self):
        self.base_pct = settings.BASE_ALLOCATION_PCT
        self.min_pct = settings.MIN_ALLOCATION_PCT
        self.max_pct = settings.MAX_ALLOCATION_PCT

    def calculate_allocation_pct(
        self,
        entry_premium: float,
        stop_loss: float,
        closed_trades: List[Dict[str, Any]],
        today_pnl: float
    ) -> Tuple[float, str]:
        """
        Determines the capital allocation percentage (5% - 20%) deterministically.
        Logs transparent reason for adjustments.
        """
        pct = self.base_pct
        reasons = [f"Base {self.base_pct}%"]

        # 1. Losing streak penalty
        losing_streak = 0
        for t in closed_trades:
            if t.get("net_pnl", 0.0) < 0:
                losing_streak += 1
            else:
                break

        if losing_streak >= 3:
            penalty = 6.0
            pct -= penalty
            reasons.append(f"-{penalty}% (Losing streak of {losing_streak})")
        elif losing_streak == 2:
            penalty = 3.0
            pct -= penalty
            reasons.append(f"-{penalty}% (2 consecutive losses)")

        # 2. Daily drawdown penalty
        if today_pnl < (-0.5 * settings.MAX_DAILY_LOSS):
            penalty = 3.0
            pct -= penalty
            reasons.append(f"-{penalty}% (Day drawdown ₹{today_pnl:,.2f})")

        # 3. Wide stop penalty
        if entry_premium > 0:
            stop_dist_pct = ((entry_premium - stop_loss) / entry_premium) * 100.0
            if stop_dist_pct > 15.0:
                penalty = 2.0
                pct -= penalty
                reasons.append(f"-{penalty}% (Wide stop {stop_dist_pct:.1f}%)")

        # 4. Long-term positive expectancy reward (minimum 30 closed trades)
        if len(closed_trades) >= 30:
            last_30 = closed_trades[:30]
            wins = sum(1 for t in last_30 if t.get("net_pnl", 0.0) > 0)
            total_net = sum(t.get("net_pnl", 0.0) for t in last_30)
            win_rate = wins / 30.0
            expectancy = total_net / 30.0

            if expectancy > 500.0 and win_rate >= 0.55:
                boost = 3.0
                pct += boost
                reasons.append(f"+{boost}% (30-trade expectancy ₹{expectancy:.0f}/trade, WR {win_rate*100:.0f}%)")

        # Clamp between min and max
        final_pct = max(self.min_pct, min(self.max_pct, pct))
        reason_str = "; ".join(reasons)
        return final_pct, reason_str

    def evaluate_entry(
        self,
        available_capital: float,
        entry_premium: float,
        stop_loss: float,
        lot_size: int,
        closed_trades: List[Dict[str, Any]],
        today_pnl: float,
        today_trade_count: int,
        bid_ask_spread_pct: float = 0.0,
        is_feed_stale: bool = False,
        is_kill_switch_active: bool = False
    ) -> Dict[str, Any]:
        """
        Evaluate risk caps and compute order quantity in lots.
        Returns dict with: allowed (bool), lots (int), quantity (int), allocation_pct, reason.
        """
        # 1. Check Hard Blockers
        if is_kill_switch_active:
            return {"allowed": False, "lots": 0, "quantity": 0, "reason": "BLOCKED: Emergency kill-switch is active"}

        if is_feed_stale:
            return {"allowed": False, "lots": 0, "quantity": 0, "reason": "BLOCKED: Market feed is stale"}

        if today_trade_count >= settings.MAX_DAILY_TRADES:
            return {
                "allowed": False,
                "lots": 0,
                "quantity": 0,
                "reason": f"BLOCKED: Max daily trades ({settings.MAX_DAILY_TRADES}) reached"
            }

        if today_pnl <= -settings.MAX_DAILY_LOSS:
            return {
                "allowed": False,
                "lots": 0,
                "quantity": 0,
                "reason": f"BLOCKED: Max daily loss (₹{settings.MAX_DAILY_LOSS:,.2f}) breached (P&L: ₹{today_pnl:,.2f})"
            }

        if bid_ask_spread_pct > settings.MAX_SPREAD_PCT:
            return {
                "allowed": False,
                "lots": 0,
                "quantity": 0,
                "reason": f"BLOCKED: Spread too wide ({bid_ask_spread_pct:.2f}% > {settings.MAX_SPREAD_PCT}%)"
            }

        if available_capital <= 0 or entry_premium <= 0 or lot_size <= 0:
            return {
                "allowed": False,
                "lots": 0,
                "quantity": 0,
                "reason": f"BLOCKED: Invalid capital (₹{available_capital}) or premium (₹{entry_premium})"
            }

        # 2. Adaptive Allocation
        alloc_pct, alloc_reason = self.calculate_allocation_pct(
            entry_premium=entry_premium,
            stop_loss=stop_loss,
            closed_trades=closed_trades,
            today_pnl=today_pnl
        )

        budget = available_capital * (alloc_pct / 100.0)
        cost_per_lot = entry_premium * lot_size

        if budget < cost_per_lot:
            return {
                "allowed": False,
                "lots": 0,
                "quantity": 0,
                "allocation_pct": alloc_pct,
                "budget": budget,
                "reason": f"SKIPPED: Budget ₹{budget:,.2f} ({alloc_pct}%) cannot afford 1 lot (₹{cost_per_lot:,.2f})"
            }

        lots = int(math.floor(budget / cost_per_lot))

        # 3. Max Lots Cap
        if lots > settings.MAX_LOTS:
            lots = settings.MAX_LOTS

        # 4. Max Loss per trade cap (2% of capital)
        risk_per_share = entry_premium - stop_loss
        if risk_per_share > 0:
            max_allowed_risk = available_capital * (settings.MAX_RISK_PER_TRADE_PCT / 100.0)
            total_risk = risk_per_share * (lots * lot_size)
            if total_risk > max_allowed_risk:
                # Shrink lots to fit max allowed risk
                fitted_lots = int(math.floor(max_allowed_risk / (risk_per_share * lot_size)))
                if fitted_lots < 1:
                    return {
                        "allowed": False,
                        "lots": 0,
                        "quantity": 0,
                        "allocation_pct": alloc_pct,
                        "budget": budget,
                        "reason": f"SKIPPED: Stop distance risk (₹{total_risk:,.2f}) exceeds max trade risk cap ₹{max_allowed_risk:,.2f}"
                    }
                lots = fitted_lots

        quantity = lots * lot_size
        return {
            "allowed": True,
            "lots": lots,
            "quantity": quantity,
            "allocation_pct": alloc_pct,
            "budget": budget,
            "reason": f"Approved: {lots} lots ({quantity} qty) at {alloc_pct}% budget ₹{budget:,.2f} [{alloc_reason}]"
        }

position_sizer = PositionSizer()
