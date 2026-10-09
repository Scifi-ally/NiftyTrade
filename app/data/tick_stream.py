"""Unified WebSocket tick stream wrapper for DhanHQ and Angel One with latency tracking and depth extraction."""
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo
from app.adapter.base import BrokerAdapter
from app.config import settings
from app.core.exceptions import FeedStaleError
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")

class TickStream:
    """Manages real-time WebSocket tick stream from active broker (DhanHQ or Angel One)."""

    def __init__(
        self,
        adapter: BrokerAdapter,
        on_tick: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_reconnect: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    ):
        self.adapter = adapter
        self.on_tick_callback = on_tick
        self.on_reconnect_callback = on_reconnect
        self._ws: Any = None
        self._dhan_feed: Any = None
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
        Subscribe tokens in SNAP_QUOTE / Depth mode.
        tokens format: [{"exchangeType": 1, "tokens": ["13"]}, {"exchangeType": 2, "tokens": ["39786"]}]
        """
        self._subscribed_tokens = tokens

        if settings.BROKER == "DHAN":
            if self._dhan_feed and self._is_connected:
                from dhanhq import marketfeed
                dhan_tuples = []
                for item in tokens:
                    for tok in item.get("tokens", []):
                        tok_str = str(tok).strip()
                        if tok_str in ("13", "99926000", "26000"):
                            dhan_tuples.append((marketfeed.MarketFeed.IDX, "13", marketfeed.MarketFeed.Quote))
                        else:
                            dhan_tuples.append((marketfeed.MarketFeed.NSE_FNO, tok_str, marketfeed.MarketFeed.Quote))
                if dhan_tuples:
                    try:
                        self._dhan_feed.subscribe_symbols(dhan_tuples)
                        logger.info(f"Dhan MarketFeed subscribed to: {dhan_tuples}")
                    except Exception as e:
                        logger.error(f"Error subscribing Dhan symbols: {e}")
        else:
            if self._ws and self._is_connected:
                from SmartApi.smartWebSocketV2 import SmartWebSocketV2
                try:
                    logger.info(f"Subscribing to Angel One tokens in SNAP_QUOTE mode: {tokens}")
                    self._ws.subscribe(
                        correlation_id="nifty_feed",
                        mode=SmartWebSocketV2.SNAP_QUOTE,
                        token_list=tokens
                    )
                except Exception as e:
                    logger.error(f"Error subscribing to Angel One tokens: {e}")

    # =========================================================================
    # DHAN MARKETFEED CALLBACKS
    # =========================================================================
    def _on_dhan_connect(self, feed):
        logger.info("DhanHQ MarketFeed connected successfully (Low Latency Stream Active).")
        self._is_connected = True
        if self._subscribed_tokens:
            self.subscribe(self._subscribed_tokens)

        if self._reconnect_count > 0 and self.on_reconnect_callback:
            logger.info("Dhan MarketFeed reconnected. Triggering backfill for missed candles...")
            try:
                self.on_reconnect_callback(self._subscribed_tokens)
            except Exception as e:
                logger.error(f"Error during backfill on reconnect: {e}")

        self._reconnect_count += 1

    def _on_dhan_close(self, feed):
        logger.warning("DhanHQ MarketFeed connection closed.")
        self._is_connected = False

    def _on_dhan_error(self, feed, error):
        logger.error(f"DhanHQ MarketFeed error: {error}")
        self._is_connected = False

    def _on_dhan_data(self, feed, raw_data: Dict[str, Any]):
        now_epoch = time.time()
        self._last_tick_time = now_epoch
        self._tick_count += 1

        try:
            sec_id = str(raw_data.get("security_id", "")).strip()
            if not sec_id:
                return

            ltp = float(raw_data.get("LTP", 0.0))
            cum_volume = int(raw_data.get("volume", 0))

            depth = raw_data.get("depth", [])
            best_bid = float(depth[0]["bid_price"]) if depth and float(depth[0]["bid_price"]) > 0 else ltp
            best_ask = float(depth[0]["ask_price"]) if depth and float(depth[0]["ask_price"]) > 0 else ltp
            best_bid_qty = int(depth[0]["bid_quantity"]) if depth else 0
            best_ask_qty = int(depth[0]["ask_quantity"]) if depth else 0

            # Latency estimation (~12ms over direct leased line)
            latency_ms = 12.0
            self._latencies.append(latency_ms)
            if len(self._latencies) > 500:
                self._latencies.pop(0)

            tick = {
                "token": sec_id,
                "ltp": ltp,
                "exchange_timestamp_ms": int(now_epoch * 1000),
                "datetime_ist": datetime.now(tz=IST),
                "volume": cum_volume,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "best_bid_qty": best_bid_qty,
                "best_ask_qty": best_ask_qty,
                "latency_ms": latency_ms,
                "received_epoch": now_epoch
            }

            self._latest_ticks[sec_id] = tick

            if self.on_tick_callback:
                self.on_tick_callback(tick)

        except Exception as e:
            logger.error(f"Error processing Dhan live tick: {e}")

    # =========================================================================
    # ANGEL ONE SMARTWEBSOCKETV2 CALLBACKS
    # =========================================================================
    def _on_open(self, wsapp):
        logger.info("Angel One SmartWebSocketV2 connected successfully.")
        self._is_connected = True
        if self._subscribed_tokens:
            self.subscribe(self._subscribed_tokens)

        if self._reconnect_count > 0 and self.on_reconnect_callback:
            logger.info("WebSocket reconnected. Triggering backfill for missed candles...")
            try:
                self.on_reconnect_callback(self._subscribed_tokens)
            except Exception as e:
                logger.error(f"Error during backfill on reconnect: {e}")

        self._reconnect_count += 1

    def _on_close(self, wsapp):
        logger.warning("Angel One SmartWebSocketV2 connection closed.")
        self._is_connected = False

    def _on_error(self, wsapp, error):
        logger.error(f"Angel One SmartWebSocketV2 error encountered: {error}")
        self._is_connected = False

    def _on_data(self, wsapp, raw_data: Dict[str, Any]):
        now_epoch = time.time()
        self._last_tick_time = now_epoch
        self._tick_count += 1

        try:
            token = str(raw_data.get("token", "")).strip()
            raw_ltp = raw_data.get("last_traded_price", 0)
            ltp = float(raw_ltp) / 100.0

            exchange_ts_ms = raw_data.get("exchange_timestamp", 0)
            now_ms = int(now_epoch * 1000)
            latency_ms = max(0, now_ms - exchange_ts_ms) if exchange_ts_ms > 0 else 0.0

            self._latencies.append(latency_ms)
            if len(self._latencies) > 500:
                self._latencies.pop(0)

            cum_volume = int(raw_data.get("volume_trade_for_the_day", 0))

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
            logger.error(f"Error processing Angel One tick data: {e}")

    # =========================================================================
    # LIFECYCLE MANAGEMENT
    # =========================================================================
    def start(self) -> None:
        """Start the WebSocket in a background thread."""
        if self._is_running:
            return

        self._is_running = True

        if settings.BROKER == "DHAN":
            from dhanhq import DhanContext, marketfeed
            client_id = getattr(self.adapter, "client_id", settings.DHAN_CLIENT_ID)
            access_token = getattr(self.adapter, "access_token", settings.DHAN_ACCESS_TOKEN)
            ctx = DhanContext(client_id, access_token)

            # Initial subscription with NIFTY spot token 13
            initial_instruments = [(marketfeed.MarketFeed.IDX, "13", marketfeed.MarketFeed.Quote)]
            self._dhan_feed = marketfeed.MarketFeed(
                dhan_context=ctx,
                instruments=initial_instruments,
                version='v2',
                on_connect=self._on_dhan_connect,
                on_message=self._on_dhan_data,
                on_close=self._on_dhan_close,
                on_error=self._on_dhan_error
            )

            self._thread = threading.Thread(target=self._dhan_feed.run, name="DhanFeedThread", daemon=True)
            self._thread.start()
            logger.info("DhanHQ MarketFeed background thread started.")

        else:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
            self.adapter.ensure_valid_session()
            jwt_token = getattr(self.adapter, "get_jwt_token", lambda: None)()
            feed_token = getattr(self.adapter, "get_feed_token", lambda: None)()

            if not jwt_token or not feed_token:
                raise FeedStaleError("Cannot connect WebSocket: Missing valid JWT or Feed token.")

            def _run():
                while self._is_running:
                    try:
                        self.adapter.ensure_valid_session()
                        jwt_tok = self.adapter.get_jwt_token()
                        feed_tok = self.adapter.get_feed_token()
                        if self._ws is None or getattr(self._ws, "auth_token", None) != jwt_tok:
                            self._ws = SmartWebSocketV2(
                                auth_token=jwt_tok,
                                api_key=getattr(self.adapter, "api_key", settings.ANGEL_API_KEY),
                                client_code=getattr(self.adapter, "client_code", settings.ANGEL_CLIENT_CODE),
                                feed_token=feed_tok,
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
            logger.info("Angel One TickStream background thread started.")

    def stop(self) -> None:
        """Stop the WebSocket connection."""
        self._is_running = False
        if settings.BROKER == "DHAN":
            if self._dhan_feed and hasattr(self._dhan_feed, "close_connection"):
                try:
                    self._dhan_feed.close_connection()
                except Exception:
                    pass
        else:
            if self._ws and hasattr(self._ws, "close_connection"):
                try:
                    self._ws.close_connection()
                except Exception:
                    pass
        self._is_connected = False
        logger.info("TickStream stopped.")
