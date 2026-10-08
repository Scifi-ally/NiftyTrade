"""Paper trading engine using real-tick fills with complete Indian charges and taxation modeling."""
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo
from app.config import settings
from app.core.logging import logger
from app.storage.db import db

IST = ZoneInfo("Asia/Kolkata")

def calculate_indian_option_charges(
    buy_price: float,
    sell_price: float,
    quantity: int,
    brokerage_per_order: float = 20.0
) -> Dict[str, float]:
    """
    Computes exact statutory charges and taxes for NSE index options:
    - Brokerage: ₹20 flat per executed order (₹40 round-trip)
    - STT: 0.1% on sell side option premium turnover
    - Exchange txn: 0.05% on total premium turnover
    - SEBI fee: ₹10 per crore (0.0001%) on total turnover
    - Stamp duty: 0.003% on buy side turnover
    - GST: 18% on (Brokerage + Exchange Txn + SEBI Fee)
    """
    buy_turnover = buy_price * quantity
    sell_turnover = sell_price * quantity
    total_turnover = buy_turnover + sell_turnover

    # 1. Brokerage (Entry + Exit)
    brokerage = brokerage_per_order * 2.0

    # 2. STT (0.1% on sell side premium turnover)
    stt = round(sell_turnover * 0.001, 2)

    # 3. Exchange transaction charges (0.05% on total premium turnover)
    exchange_charges = round(total_turnover * 0.0005, 2)

    # 4. SEBI turnover fee (₹10 / crore)
    sebi_charges = round(total_turnover * 0.000001, 2)

    # 5. Stamp duty (0.003% on buy turnover)
    stamp_duty = round(buy_turnover * 0.00003, 2)

    # 6. GST (18% on Brokerage + Exchange charges + SEBI charges)
    gst = round((brokerage + exchange_charges + sebi_charges) * 0.18, 2)

    total_charges = round(brokerage + stt + exchange_charges + sebi_charges + stamp_duty + gst, 2)

    return {
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charges": exchange_charges,
        "sebi_charges": sebi_charges,
        "stamp_duty": stamp_duty,
        "gst": gst,
        "total_charges": total_charges
    }


class PaperTradingEngine:
    """
    Simulates order fills strictly on real best ask/bid from the live tick stream.
    Manages active paper positions, charges, and real-time unrealised P&L.
    """

    def __init__(self):
        self.active_trade: Optional[Dict[str, Any]] = None
        self._load_active_trade()

    def _load_active_trade(self) -> None:
        """Load any open trade from SQLite on startup."""
        open_t = db.get_open_trade()
        if open_t and open_t.get("mode") == "PAPER":
            self.active_trade = open_t
            logger.info(f"Loaded existing open paper trade: {self.active_trade['trade_id']} ({self.active_trade['symbol']})")

    @property
    def has_open_position(self) -> bool:
        return self.active_trade is not None

    def execute_entry(
        self,
        token: str,
        symbol: str,
        option_type: str,
        quantity: int,
        tick: Dict[str, Any],
        stop_loss: float,
        target: float
    ) -> Dict[str, Any]:
        """
        Execute paper buy order filled at real best ask (or LTP + slippage if depth missing).
        """
        if self.has_open_position:
            logger.warning("Paper entry rejected: Another position is already open.")
            return {}

        now_dt = datetime.now(tz=IST)
        best_ask = tick.get("best_ask", 0.0)
        ltp = tick.get("ltp", 0.0)

        # Realistic fill: Best Ask or LTP + slippage
        if best_ask > 0 and abs(best_ask - ltp) / ltp < 0.10:
            fill_price = best_ask
        else:
            fill_price = round(ltp + settings.SLIPPAGE_POINTS, 2)

        trade_id = f"PAPER_{uuid.uuid4().hex[:8].upper()}"
        order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"

        # Record Order
        order_record = {
            "order_id": order_id,
            "mode": "PAPER",
            "token": token,
            "symbol": symbol,
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "quantity": quantity,
            "price": fill_price,
            "status": "FILLED",
            "created_at": now_dt.isoformat(),
            "filled_at": now_dt.isoformat(),
            "fill_price": fill_price,
            "latency_ms": tick.get("latency_ms", 0.0)
        }
        db.save_order(order_record)

        # Record Trade
        trade_record = {
            "trade_id": trade_id,
            "mode": "PAPER",
            "token": token,
            "symbol": symbol,
            "option_type": option_type,
            "quantity": quantity,
            "entry_time": now_dt.isoformat(),
            "entry_price": fill_price,
            "initial_stop": stop_loss,
            "current_stop": stop_loss,
            "target": target,
            "exit_time": None,
            "exit_price": None,
            "exit_reason": None,
            "gross_pnl": 0.0,
            "charges": 0.0,
            "net_pnl": 0.0,
            "status": "OPEN"
        }
        db.save_trade(trade_record)
        self.active_trade = trade_record

        logger.info(
            f"[PAPER FILL] BOUGHT {quantity} {symbol} @ Rs. {fill_price:.2f} | "
            f"SL: Rs. {stop_loss:.2f} | Target: Rs. {target:.2f} (TradeID: {trade_id})"
        )
        return trade_record

    def execute_exit(
        self,
        tick: Dict[str, Any],
        reason: str
    ) -> Optional[Dict[str, Any]]:
        """
        Execute paper sell order filled at real best bid (or LTP - slippage if depth missing).
        Computes gross P&L, real Indian taxes & charges, and net P&L.
        """
        if not self.active_trade:
            return None

        now_dt = datetime.now(tz=IST)
        best_bid = tick.get("best_bid", 0.0)
        ltp = tick.get("ltp", 0.0)

        # Realistic fill: Best Bid or LTP - slippage
        if best_bid > 0 and abs(best_bid - ltp) / ltp < 0.10:
            fill_price = best_bid
        else:
            fill_price = max(0.05, round(ltp - settings.SLIPPAGE_POINTS, 2))

        trade = self.active_trade
        qty = trade["quantity"]
        entry_price = trade["entry_price"]

        gross_pnl = round((fill_price - entry_price) * qty, 2)
        charge_details = calculate_indian_option_charges(
            buy_price=entry_price,
            sell_price=fill_price,
            quantity=qty
        )
        total_charges = charge_details["total_charges"]
        net_pnl = round(gross_pnl - total_charges, 2)

        order_id = f"ORD_{uuid.uuid4().hex[:8].upper()}"
        order_record = {
            "order_id": order_id,
            "mode": "PAPER",
            "token": trade["token"],
            "symbol": trade["symbol"],
            "transaction_type": "SELL",
            "order_type": "LIMIT",
            "quantity": qty,
            "price": fill_price,
            "status": "FILLED",
            "created_at": now_dt.isoformat(),
            "filled_at": now_dt.isoformat(),
            "fill_price": fill_price,
            "latency_ms": tick.get("latency_ms", 0.0)
        }
        db.save_order(order_record)

        trade["exit_time"] = now_dt.isoformat()
        trade["exit_price"] = fill_price
        trade["exit_reason"] = reason
        trade["gross_pnl"] = gross_pnl
        trade["charges"] = total_charges
        trade["net_pnl"] = net_pnl
        trade["status"] = "CLOSED"

        db.save_trade(trade)
        closed_trade = dict(trade)
        self.active_trade = None

        logger.info(
            f"[PAPER EXIT] SOLD {qty} {closed_trade['symbol']} @ Rs. {fill_price:.2f} [{reason}] | "
            f"Gross: Rs. {gross_pnl:+,.2f} | Charges: Rs. {total_charges:,.2f} | Net: Rs. {net_pnl:+,.2f}"
        )
        return closed_trade

    def update_stop_loss(self, new_stop: float) -> None:
        """Update current stop loss (for trailing stop)."""
        if self.active_trade and new_stop > self.active_trade["current_stop"]:
            old_sl = self.active_trade["current_stop"]
            self.active_trade["current_stop"] = new_stop
            db.save_trade(self.active_trade)
            logger.info(f"[PAPER TRAIL] SL moved up: Rs. {old_sl:.2f} -> Rs. {new_stop:.2f}")

    def calculate_unrealised_pnl(self, current_ltp: float) -> Dict[str, float]:
        """Compute live unrealised gross P&L, estimated charges, and net P&L."""
        if not self.active_trade or current_ltp <= 0:
            return {"unrealised_gross": 0.0, "unrealised_charges": 0.0, "unrealised_net": 0.0}

        qty = self.active_trade["quantity"]
        entry_price = self.active_trade["entry_price"]
        gross = round((current_ltp - entry_price) * qty, 2)
        charges = calculate_indian_option_charges(entry_price, current_ltp, qty)["total_charges"]
        net = round(gross - charges, 2)
        return {
            "unrealised_gross": gross,
            "unrealised_charges": charges,
            "unrealised_net": net
        }

paper_engine = PaperTradingEngine()
