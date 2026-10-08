"""Live trading execution engine placing real orders via Angel One SmartAPI."""
import uuid
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from app.adapter.angel_one import AngelOneAdapter
from app.config import settings
from app.core.exceptions import OrderExecutionError
from app.core.logging import logger
from app.storage.db import db

IST = ZoneInfo("Asia/Kolkata")

class LiveTradingEngine:
    """
    Executes real live orders via Angel One SmartAPI.
    Uses marketable LIMIT orders (LTP + buffer).
    Handles exchange freeze quantity splitting and position reconciliation.
    """

    def __init__(self, adapter: AngelOneAdapter):
        self.adapter = adapter
        self.active_trade: Optional[Dict[str, Any]] = None
        self.protective_sl_order_id: Optional[str] = None
        self._load_active_trade()

    def _load_active_trade(self) -> None:
        open_t = db.get_open_trade()
        if open_t and open_t.get("mode") == "LIVE":
            self.active_trade = open_t
            logger.info(f"Loaded existing open LIVE trade: {self.active_trade['trade_id']} ({self.active_trade['symbol']})")

    @property
    def has_open_position(self) -> bool:
        return self.active_trade is not None

    def reconcile_positions(self) -> None:
        """Fetch broker open positions and verify against internal state."""
        try:
            positions = self.adapter.get_positions()
            logger.info(f"Reconciled broker positions: {len(positions)} positions found.")
            for pos in positions:
                net_qty = int(pos.get("netqty", 0))
                if net_qty != 0:
                    sym = pos.get("tradingsymbol", "")
                    logger.warning(f"Existing open broker position detected: {sym} (Net Qty: {net_qty})")
        except Exception as e:
            logger.error(f"Position reconciliation error: {e}")

    def execute_entry(
        self,
        token: str,
        symbol: str,
        option_type: str,
        quantity: int,
        tick: Dict[str, Any],
        stop_loss: float,
        target: float,
        freeze_qty: int = 3510
    ) -> Optional[Dict[str, Any]]:
        """
        Place marketable limit buy order via Angel One.
        Splits order if quantity exceeds freeze_qty.
        """
        if self.has_open_position:
            logger.warning("LIVE entry blocked: Another position is currently active.")
            return None

        ltp = float(tick.get("ltp", 0.0))
        # Marketable limit: LTP + buffer (capped to ensure immediate execution)
        limit_price = round(ltp + settings.LIMIT_BUFFER_POINTS, 2)

        # 1. Split into freeze chunks if necessary
        chunks: List[int] = []
        rem = quantity
        while rem > 0:
            chunk = min(rem, freeze_qty)
            chunks.append(chunk)
            rem -= chunk

        placed_order_ids = []
        fill_price = limit_price
        start_time = time.time()

        for chunk_qty in chunks:
            try:
                order_id = self.adapter.place_order(
                    symbol=symbol,
                    token=token,
                    exchange="NFO",
                    transaction_type="BUY",
                    order_type="LIMIT",
                    quantity=chunk_qty,
                    price=limit_price,
                    product_type="INTRADAY"
                )
                if order_id:
                    placed_order_ids.append(order_id)
                else:
                    raise OrderExecutionError("Broker returned empty order ID.")
            except Exception as e:
                logger.error(f"Live order chunk placement failed: {e}")
                # Cancel any placed chunks if partial failure
                for oid in placed_order_ids:
                    try:
                        self.adapter.cancel_order(oid)
                    except Exception:
                        pass
                raise OrderExecutionError(f"Live entry failed: {e}")

        latency_ms = round((time.time() - start_time) * 1000, 1)
        now_dt = datetime.now(tz=IST)
        trade_id = f"LIVE_{uuid.uuid4().hex[:8].upper()}"

        # Record primary order in SQLite
        db.save_order({
            "order_id": placed_order_ids[0],
            "mode": "LIVE",
            "token": token,
            "symbol": symbol,
            "transaction_type": "BUY",
            "order_type": "LIMIT",
            "quantity": quantity,
            "price": limit_price,
            "status": "FILLED",
            "created_at": now_dt.isoformat(),
            "filled_at": now_dt.isoformat(),
            "fill_price": fill_price,
            "latency_ms": latency_ms
        })

        trade_record = {
            "trade_id": trade_id,
            "mode": "LIVE",
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

        # Place broker-side protective SL order if enabled
        try:
            sl_limit_price = max(0.05, round(stop_loss - 1.0, 2))
            sl_id = self.adapter.place_order(
                symbol=symbol,
                token=token,
                exchange="NFO",
                transaction_type="SELL",
                order_type="STOPLOSS_LIMIT",
                quantity=quantity,
                price=sl_limit_price,
                trigger_price=stop_loss,
                product_type="INTRADAY"
            )
            self.protective_sl_order_id = sl_id
            logger.info(f"Protective broker-side SL order placed: {sl_id} @ Trigger: {stop_loss}")
        except Exception as e:
            logger.warning(f"Could not place broker-side protective SL order: {e}. Bot will manage stop internally.")

        logger.info(f"[LIVE ORDER FILLED] BOUGHT {quantity} {symbol} @ Rs. {fill_price:.2f} (Latency: {latency_ms}ms)")
        return trade_record

    def execute_exit(
        self,
        tick: Dict[str, Any],
        reason: str,
        freeze_qty: int = 3510
    ) -> Optional[Dict[str, Any]]:
        """Place marketable limit sell order via Angel One to square off position."""
        if not self.active_trade:
            return None

        trade = self.active_trade
        token = trade["token"]
        symbol = trade["symbol"]
        qty = trade["quantity"]
        entry_price = trade["entry_price"]

        ltp = float(tick.get("ltp", 0.0))
        limit_price = max(0.05, round(ltp - settings.LIMIT_BUFFER_POINTS, 2))

        # Cancel broker-side protective SL order first
        if self.protective_sl_order_id:
            try:
                self.adapter.cancel_order(self.protective_sl_order_id)
            except Exception:
                pass
            self.protective_sl_order_id = None

        chunks: List[int] = []
        rem = qty
        while rem > 0:
            chunk = min(rem, freeze_qty)
            chunks.append(chunk)
            rem -= chunk

        exit_order_ids = []
        start_time = time.time()

        for chunk_qty in chunks:
            try:
                order_id = self.adapter.place_order(
                    symbol=symbol,
                    token=token,
                    exchange="NFO",
                    transaction_type="SELL",
                    order_type="LIMIT",
                    quantity=chunk_qty,
                    price=limit_price,
                    product_type="INTRADAY"
                )
                if order_id:
                    exit_order_ids.append(order_id)
            except Exception as e:
                logger.error(f"Live exit chunk order failed: {e}")

        latency_ms = round((time.time() - start_time) * 1000, 1)
        now_dt = datetime.now(tz=IST)
        fill_price = limit_price

        # Import charges calculation
        from app.execution.paper_engine import calculate_indian_option_charges
        gross_pnl = round((fill_price - entry_price) * qty, 2)
        charges = calculate_indian_option_charges(entry_price, fill_price, qty)["total_charges"]
        net_pnl = round(gross_pnl - charges, 2)

        if exit_order_ids:
            db.save_order({
                "order_id": exit_order_ids[0],
                "mode": "LIVE",
                "token": token,
                "symbol": symbol,
                "transaction_type": "SELL",
                "order_type": "LIMIT",
                "quantity": qty,
                "price": fill_price,
                "status": "FILLED",
                "created_at": now_dt.isoformat(),
                "filled_at": now_dt.isoformat(),
                "fill_price": fill_price,
                "latency_ms": latency_ms
            })

        trade["exit_time"] = now_dt.isoformat()
        trade["exit_price"] = fill_price
        trade["exit_reason"] = reason
        trade["gross_pnl"] = gross_pnl
        trade["charges"] = charges
        trade["net_pnl"] = net_pnl
        trade["status"] = "CLOSED"

        db.save_trade(trade)
        closed_trade = dict(trade)
        self.active_trade = None

        logger.info(
            f"[LIVE EXIT FILLED] SOLD {qty} {symbol} @ Rs. {fill_price:.2f} [{reason}] | "
            f"Net: Rs. {net_pnl:+,.2f} (Latency: {latency_ms}ms)"
        )
        return closed_trade

    def update_stop_loss(self, new_stop: float) -> None:
        """Modify broker-side protective SL order when trailing stop moves."""
        if self.active_trade and new_stop > self.active_trade["current_stop"]:
            self.active_trade["current_stop"] = new_stop
            db.save_trade(self.active_trade)
            if self.protective_sl_order_id:
                try:
                    sl_limit = max(0.05, round(new_stop - 1.0, 2))
                    self.adapter.modify_order(
                        order_id=self.protective_sl_order_id,
                        quantity=self.active_trade["quantity"],
                        price=sl_limit,
                        trigger_price=new_stop,
                        order_type="STOPLOSS_LIMIT"
                    )
                    logger.info(f"Broker-side protective SL modified to Rs. {new_stop:.2f}")
                except Exception as e:
                    logger.warning(f"Could not modify broker-side SL order: {e}")
