"""Angel One SmartWebSocketV2 wrapper for streaming real live ticks, best bid/ask, and latency tracking."""
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from app.adapter.angel_one import AngelOneAdapter
from app.config import settings
from app.core.exceptions import FeedStaleError
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")

class TickStream:
    """Manages real-time WebSocket tick stream from Angel One."""

    def __init__(
        self,
        adapter: AngelOneAdapter,
        on_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_reconnect: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    ):
        self.adapter = adapter
        self.on_tick_callback = on_tick
        self.on_reconnect_callback = on_reconnect
        self._ws: Optional[SmartWebSocketV2] = None
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._is_connected = False
        self._reconnect_count = 0
        self._subscribed_tokens: List[Dict[str, Any]] = []

        # Real-time state
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}  # token -> tick dict
        self._last_tick_time: Optional[float] = None
        self._tick_count = 0
        self._latencies: List[float] = []

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_stale(self) -> bool:
        """Check if feed has stopped receiving ticks for STALE_FEED_SECONDS."""
        if not self._is_connected or self._last_tick_time is None:
            return True
        return (time.time() - self._last_tick_time) > settings.STALE_FEED_SECONDS

    def get_latest_tick(self, token: str) -> Optional[Dict[str, Any]]:
        return self._latest_ticks.get(str(token))

    def get_stats(self) -> Dict[str, Any]:
        avg_lat = sum(self._latencies[-100:]) / max(1, len(self._latencies[-100:])) if self._latencies else 0.0
        return {
            "is_connected": self._is_connected,
            "is_stale": self.is_stale,
            "tick_count": self._tick_count,
            "last_tick_time": self._last_tick_time,
            "avg_latency_ms": round(avg_lat, 2)
        }

    def subscribe(self, tokens: List[Dict[str, Any]]) -> None:
        """
        Subscribe tokens in SNAP_QUOTE mode (Mode 3) for depth and best bid/ask.
        tokens format: [{"exchangeType": 1, "tokens": ["99926000"]}, {"exchangeType": 2, "tokens": ["39786"]}]
        """
        self._subscribed_tokens = tokens
        if self._ws and self._is_connected:
            try:
                logger.info(f"Subscribing to tokens in SNAP_QUOTE mode: {tokens}")
                self._ws.subscribe(
                    correlation_id="nifty_feed",
                    mode=SmartWebSocketV2.SNAP_QUOTE,
                    token_list=tokens
                )
            except Exception as e:
                logger.error(f"Error subscribing to tokens: {e}")

    def _on_open(self, wsapp):
        logger.info("SmartWebSocketV2 connected successfully.")
        self._is_connected = True
        if self._subscribed_tokens:
            try:
                self._ws.subscribe(
                    correlation_id="nifty_feed",
                    mode=SmartWebSocketV2.SNAP_QUOTE,
                    token_list=self._subscribed_tokens
                )
            except Exception as e:
                logger.error(f"Failed to subscribe on open: {e}")

        # If reconnecting after a drop, trigger historical backfill
        if self._reconnect_count > 0 and self.on_reconnect_callback:
            logger.info("WebSocket reconnected. Triggering backfill for missed candles...")
            try:
                self.on_reconnect_callback(self._subscribed_tokens)
            except Exception as e:
                logger.error(f"Error during backfill on reconnect: {e}")

        self._reconnect_count += 1

    def _on_close(self, wsapp):
        logger.warning("SmartWebSocketV2 connection closed.")
        self._is_connected = False

    def _on_error(self, wsapp, error):
        logger.error(f"SmartWebSocketV2 error encountered: {error}")
        self._is_connected = False

    def _on_data(self, wsapp, raw_data: Dict[str, Any]):
        """Callback executed on every received binary tick."""
        now_epoch = time.time()
        self._last_tick_time = now_epoch
        self._tick_count += 1

        try:
            token = str(raw_data.get("token", "")).strip()
            # LTP is in paise -> convert to rupees
            raw_ltp = raw_data.get("last_traded_price", 0)
            ltp = float(raw_ltp) / 100.0

            # Exchange timestamp in ms
            exchange_ts_ms = raw_data.get("exchange_timestamp", 0)
            now_ms = int(now_epoch * 1000)
            latency_ms = max(0, now_ms - exchange_ts_ms) if exchange_ts_ms > 0 else 0.0

            self._latencies.append(latency_ms)
            if len(self._latencies) > 500:
                self._latencies.pop(0)

            # Cumulative volume
            cum_volume = int(raw_data.get("volume_trade_for_the_day", 0))

            # Best Bid and Best Ask extraction from depth
            best_bid = ltp
            best_ask = ltp
            best_bid_qty = 0
            best_ask_qty = 0

            b5_buy = raw_data.get("best_5_buy_data", [])
            b5_sell = raw_data.get("best_5_sell_data", [])

            valid_buy = [b for b in b5_buy if b.get("price", 0) > 0]
            valid_sell = [s for s in b5_sell if s.get("price", 0) > 0]

            if valid_buy and valid_sell:
                p_buy0 = valid_buy[0]["price"] / 100.0
                p_sell0 = valid_sell[0]["price"] / 100.0

                if p_buy0 <= p_sell0:
                    best_bid = p_buy0
                    best_bid_qty = valid_buy[0]["quantity"]
                    best_ask = p_sell0
                    best_ask_qty = valid_sell[0]["quantity"]
                else:
                    # Inverted depth from SDK
                    best_bid = p_sell0
                    best_bid_qty = valid_sell[0]["quantity"]
                    best_ask = p_buy0
                    best_ask_qty = valid_buy[0]["quantity"]

            elif valid_buy:
                best_bid = valid_buy[0]["price"] / 100.0
                best_bid_qty = valid_buy[0]["quantity"]
            elif valid_sell:
                best_ask = valid_sell[0]["price"] / 100.0
                best_ask_qty = valid_sell[0]["quantity"]

            tick = {
                "token": token,
                "ltp": ltp,
                "exchange_timestamp_ms": exchange_ts_ms,
                "datetime_ist": datetime.fromtimestamp(exchange_ts_ms / 1000.0, tz=IST) if exchange_ts_ms > 0 else datetime.now(tz=IST),
                "volume": cum_volume,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "best_bid_qty": best_bid_qty,
                "best_ask_qty": best_ask_qty,
                "latency_ms": latency_ms,
                "received_epoch": now_epoch
            }

            self._latest_ticks[token] = tick

            if self.on_tick_callback:
                self.on_tick_callback(tick)

        except Exception as e:
            logger.error(f"Error processing tick data: {e}")

    def start(self) -> None:
        """Start the WebSocket in a background thread."""
        if self._is_running:
            return

        self.adapter.ensure_valid_session()
        jwt_token = self.adapter.get_jwt_token()
        feed_token = self.adapter.get_feed_token()

        if not jwt_token or not feed_token:
            raise FeedStaleError("Cannot connect WebSocket: Missing valid JWT or Feed token.")

        self._ws = SmartWebSocketV2(
            auth_token=jwt_token,
            api_key=self.adapter.api_key,
            client_code=self.adapter.client_code,
            feed_token=feed_token,
            max_retry_attempt=5,
            retry_strategy=1,
            retry_delay=5
        )

        self._ws.on_open = self._on_open
        self._ws.on_close = self._on_close
        self._ws.on_error = self._on_error
        self._ws.on_data = self._on_data

        self._is_running = True

        def _run():
            while self._is_running:
                try:
                    # Auto-refresh session and tokens before (re)connecting
                    self.adapter.ensure_valid_session()
                    jwt_token = self.adapter.get_jwt_token()
                    feed_token = self.adapter.get_feed_token()
                    if self._ws is None or getattr(self._ws, "auth_token", None) != jwt_token:
                        self._ws = SmartWebSocketV2(
                            auth_token=jwt_token,
                            api_key=self.adapter.api_key,
                            client_code=self.adapter.client_code,
                            feed_token=feed_token,
                            max_retry_attempt=5,
                            retry_strategy=1,
                            retry_delay=5
                        )
                        self._ws.on_open = self._on_open
                        self._ws.on_close = self._on_close
                        self._ws.on_error = self._on_error
                        self._ws.on_data = self._on_data
                    self._ws.connect()
                except Exception as e:
                    logger.warning(f"WebSocket connect error: {e}. Reconnecting in 5s...")
                    time.sleep(5)

        self._thread = threading.Thread(target=_run, name="TickStreamThread", daemon=True)
        self._thread.start()
        logger.info("TickStream background thread started.")

    def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._is_running = False
        if self._ws and hasattr(self._ws, "close_connection"):
            try:
                self._ws.close_connection()
            except Exception:
                pass
        self._is_connected = False
        logger.info("TickStream stopped.")
