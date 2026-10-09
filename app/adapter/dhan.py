"""DhanHQ adapter implementing BrokerAdapter with high rate limits, low latency, and 30-day token auth."""
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from pathlib import Path
from zoneinfo import ZoneInfo
import pyotp
from dhanhq import DhanContext, dhanhq, DhanLogin
from app.adapter.base import BrokerAdapter
from app.config import settings
from app.core.exceptions import AuthenticationError, HistoricalDataError, OrderExecutionError
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")
CACHE_FILE = Path(__file__).resolve().parent.parent.parent / ".dhan_token"

class DhanAdapter(BrokerAdapter):
    """Adapter for DhanHQ API v2 providing high rate limits and low-latency execution."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        pin: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ):
        self.client_id = client_id if client_id is not None else settings.DHAN_CLIENT_ID
        self.pin = pin if pin is not None else settings.DHAN_PIN
        self.totp_secret = totp_secret if totp_secret is not None else settings.DHAN_TOTP_SECRET
        if access_token is not None:
            self.access_token = access_token
        else:
            self.access_token = settings.DHAN_ACCESS_TOKEN or self._load_cached_token() or ""
        self._context: Optional[DhanContext] = None
        self._client: Optional[dhanhq] = None
        self._is_logged_in: bool = False
        self._last_login_time: Optional[datetime] = None

    def _load_cached_token(self) -> Optional[str]:
        """Load locally cached token if less than 23 hours old."""
        if not CACHE_FILE.exists():
            return None
        try:
            content = CACHE_FILE.read_text(encoding="utf-8").strip()
            if content:
                mtime = CACHE_FILE.stat().st_mtime
                if (time.time() - mtime) < 23 * 3600:
                    return content
        except Exception:
            pass
        return None

    def _save_cached_token(self, token: str) -> None:
        """Cache active access token locally to prevent unnecessary TOTP requests."""
        try:
            CACHE_FILE.write_text(token.strip(), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not cache Dhan token to file: {e}")

    def _generate_access_token_via_totp(self) -> Optional[str]:
        """Automatically generate fresh 24-hour Dhan access token using PIN and TOTP."""
        if not self.client_id or not self.pin or not self.totp_secret:
            return None
        try:
            totp_code = pyotp.TOTP(self.totp_secret.strip()).now()
            logger.info("Attempting automated DhanHQ login with PIN & TOTP...")
            dhan_login = DhanLogin(self.client_id.strip())
            resp = dhan_login.generate_token(self.pin.strip(), totp_code)

            token = None
            if isinstance(resp, dict):
                token = resp.get("accessToken") or resp.get("data", {}).get("accessToken")

            if token:
                logger.info("Automated DhanHQ token generation succeeded! New 24-hour token acquired.")
                self.access_token = token
                self._save_cached_token(token)
                return token
            elif isinstance(resp, dict) and "once every 2 minutes" in resp.get("message", ""):
                cached = self._load_cached_token()
                if cached:
                    logger.info("Using recently generated cached Dhan token.")
                    self.access_token = cached
                    return cached
            logger.warning(f"DhanHQ TOTP login returned unexpected response: {resp}")
            return None
        except Exception as e:
            logger.error(f"Failed to generate DhanHQ token via PIN/TOTP: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to generate DhanHQ token via PIN/TOTP: {e}")
            return None

    def login(self, max_retries: int = 3, retry_delay: float = 1.0) -> bool:
        """Authenticate with DhanHQ. Supports one-time automated PIN+TOTP or existing access token."""
        if not self.client_id:
            raise AuthenticationError("Missing required DHAN_CLIENT_ID in configuration.")

        if not self.access_token:
            if self.pin and self.totp_secret:
                self._generate_access_token_via_totp()

            if not self.access_token:
                raise AuthenticationError(
                    "Missing Dhan credentials: provide either DHAN_ACCESS_TOKEN or (DHAN_PIN and DHAN_TOTP_SECRET) in .env"
                )

        logger.info(f"Authenticating with DhanHQ for Client ID: {self.client_id}...")

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                self._context = DhanContext(self.client_id, self.access_token)
                self._client = dhanhq(self._context)

                # Validate token by fetching funds limit
                resp = self._client.get_fund_limits()
                status = resp.get("status", "").lower() if isinstance(resp, dict) else ""
                
                # Success condition in Dhan API: status == 'success' or data has 'availabelBalance'
                if status == "success" or (isinstance(resp, dict) and "data" in resp):
                    self._is_logged_in = True
                    self._last_login_time = datetime.now(tz=IST)
                    logger.info("DhanHQ authentication successful! Session active.")
                    return True

                # If token was rejected/expired and we have PIN + TOTP, refresh automatically
                if attempt == 1 and self.pin and self.totp_secret:
                    logger.info("Dhan token invalid or expired. Generating fresh token via TOTP...")
                    new_token = self._generate_access_token_via_totp()
                    if new_token:
                        continue

                err_msg = resp.get("remarks") or resp.get("message") or str(resp) if isinstance(resp, dict) else str(resp)
                last_error = f"Dhan auth rejected: {err_msg}"
                logger.warning(f"Dhan login attempt {attempt}/{max_retries} failed: {last_error}")

            except Exception as e:
                if attempt == 1 and self.pin and self.totp_secret:
                    logger.info("Dhan auth exception encountered. Attempting fresh TOTP login...")
                    new_token = self._generate_access_token_via_totp()
                    if new_token:
                        continue
                last_error = str(e)
                logger.warning(f"Dhan login attempt {attempt}/{max_retries} encountered exception: {e}")

            if attempt < max_retries:
                time.sleep(retry_delay * attempt)

        self._is_logged_in = False
        raise AuthenticationError(f"Failed to authenticate with DhanHQ after {max_retries} attempts: {last_error}")

    def is_logged_in(self) -> bool:
        """Dhan tokens are valid for up to 24 hours."""
        if not self._is_logged_in or self._client is None:
            return False
        if self._last_login_time:
            # Consider token stale after 23 hours to trigger automated refresh before hard expiry
            elapsed_hours = (datetime.now(tz=IST) - self._last_login_time).total_seconds() / 3600.0
            if elapsed_hours >= 23.0:
                return False
        return True

    def get_feed_token(self) -> Optional[str]:
        """Return access token for WebSocket streaming."""
        return self.access_token

    def ensure_valid_session(self) -> None:
        if not self.is_logged_in():
            self.login()

    def get_funds(self) -> float:
        """Fetch available margin from DhanHQ."""
        self.ensure_valid_session()
        try:
            resp = self._client.get_fund_limits()
            if isinstance(resp, dict):
                data = resp.get("data", resp)
                val = data.get("availabelBalance", data.get("availableMargin", data.get("sodLimit", 0.0)))
                funds = float(val)
                logger.info(f"Fetched real Dhan available margin: Rs. {funds:,.2f}")
                return funds
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching funds from DhanHQ: {e}")
            raise

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch current open positions from Dhan."""
        self.ensure_valid_session()
        try:
            resp = self._client.get_positions()
            if isinstance(resp, dict):
                return resp.get("data", []) or []
            if isinstance(resp, list):
                return resp
            return []
        except Exception as e:
            logger.error(f"Error fetching positions from DhanHQ: {e}")
            return []

    def get_orders(self) -> List[Dict[str, Any]]:
        """Fetch order book from Dhan."""
        self.ensure_valid_session()
        try:
            resp = self._client.get_order_list()
            if isinstance(resp, dict):
                return resp.get("data", []) or []
            if isinstance(resp, list):
                return resp
            return []
        except Exception as e:
            logger.error(f"Error fetching order book from DhanHQ: {e}")
            return []

    def get_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str,
        max_retries: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candle data from DhanHQ intraday minute API.
        Intervals: ONE_MINUTE (1), FIVE_MINUTE (5), FIFTEEN_MINUTE (15)
        """
        self.ensure_valid_session()

        sec_id = str(symbol_token)
        is_index = sec_id in ("13", "99926000", "26000") or exchange.upper() in ("IDX", "INDEX")

        segment = "IDX_I" if is_index else "NSE_FNO"
        instrument = "INDEX" if is_index else "OPTIDX"

        int_map = {
            "ONE_MINUTE": 1,
            "THREE_MINUTE": 1,
            "FIVE_MINUTE": 5,
            "TEN_MINUTE": 5,
            "FIFTEEN_MINUTE": 15
        }
        interval_num = int_map.get(interval.upper(), 1)

        # Dhan expects dates in YYYY-MM-DD
        from_clean = from_date.split(" ")[0].strip()
        to_clean = to_date.split(" ")[0].strip()

        for attempt in range(1, max_retries + 1):
            try:
                resp = self._client.intraday_minute_data(
                    security_id=sec_id,
                    exchange_segment=segment,
                    instrument_type=instrument,
                    from_date=from_clean,
                    to_date=to_clean,
                    interval=interval_num
                )

                if isinstance(resp, dict):
                    remarks = resp.get("remarks")
                    if isinstance(remarks, dict) and remarks.get("error_code") == "DH-902":
                        err_msg = remarks.get("error_message", "Historical Data requires DhanHQ PLUS subscription")
                        logger.warning(f"Dhan historical data unavailable: {err_msg}")
                        raise HistoricalDataError(err_msg)

                    data = resp.get("data")
                    if isinstance(data, dict):
                        # Dhan returns dictionary of parallel arrays: open, high, low, close, volume, start_Time / timestamp
                        opens = data.get("open", [])
                        highs = data.get("high", [])
                        lows = data.get("low", [])
                        closes = data.get("close", [])
                        vols = data.get("volume", [])
                        times = data.get("start_Time") or data.get("timestamp") or []

                        if opens and closes and len(opens) == len(closes):
                            candles = []
                            for i in range(len(opens)):
                                ts_val = times[i] if i < len(times) else i
                                if isinstance(ts_val, (int, float)):
                                    try:
                                        ts_str = datetime.fromtimestamp(ts_val, tz=IST).strftime("%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        ts_str = str(ts_val)
                                else:
                                    ts_str = str(ts_val)

                                candles.append({
                                    "timestamp": ts_str,
                                    "open": float(opens[i]),
                                    "high": float(highs[i]),
                                    "low": float(lows[i]),
                                    "close": float(closes[i]),
                                    "volume": float(vols[i]) if i < len(vols) else 0.0
                                })
                            return candles

                logger.warning(f"Dhan candle fetch attempt {attempt} returned non-standard data: {resp}")

            except HistoricalDataError:
                raise
            except Exception as e:
                logger.warning(f"Dhan candle fetch attempt {attempt} encountered exception: {e}")

            time.sleep(0.3 * attempt)

        raise HistoricalDataError(f"Failed to fetch historical candles from DhanHQ for token {sec_id} ({from_date} to {to_date})")

    def place_order(
        self,
        symbol: str,
        token: str,
        exchange: str,
        transaction_type: str,
        order_type: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
        product_type: str = "INTRADAY"
    ) -> Optional[str]:
        """Place order with DhanHQ."""
        self.ensure_valid_session()

        sec_id = str(token)
        dhan_trans = dhanhq.BUY if transaction_type.upper() == "BUY" else dhanhq.SELL
        dhan_ord_type = dhanhq.MARKET if order_type.upper() == "MARKET" else dhanhq.LIMIT
        dhan_prod = dhanhq.INTRA

        try:
            resp = self._client.place_order(
                security_id=sec_id,
                exchange_segment=dhanhq.NSE_FNO,
                transaction_type=dhan_trans,
                quantity=quantity,
                order_type=dhan_ord_type,
                product_type=dhan_prod,
                price=round(price, 2) if price > 0 else 0.0,
                trigger_price=round(trigger_price, 2) if trigger_price > 0 else 0.0
            )

            if isinstance(resp, dict):
                data = resp.get("data", resp)
                order_id = data.get("orderId") or data.get("order_id")
                if order_id:
                    logger.info(f"Dhan live order placed: OrderID={order_id}, Symbol={symbol}, Qty={quantity}, Price={price}")
                    return str(order_id)

            err_msg = resp.get("remarks") or resp.get("message") if isinstance(resp, dict) else str(resp)
            raise OrderExecutionError(f"Dhan rejected order: {err_msg}")

        except Exception as e:
            logger.error(f"Error placing order via DhanHQ: {e}")
            raise OrderExecutionError(f"Order placement failed: {e}")

    def modify_order(
        self,
        order_id: str,
        quantity: int,
        price: float,
        trigger_price: float = 0.0,
        order_type: str = "LIMIT"
    ) -> bool:
        self.ensure_valid_session()
        try:
            dhan_ord_type = dhanhq.LIMIT if order_type.upper() == "LIMIT" else dhanhq.MARKET
            resp = self._client.modify_order(
                order_id=str(order_id),
                order_type=dhan_ord_type,
                leg_name="ENTRY_LEG",
                quantity=quantity,
                price=round(price, 2),
                trigger_price=round(trigger_price, 2)
            )
            return bool(isinstance(resp, dict) and resp.get("status", "").lower() == "success")
        except Exception as e:
            logger.error(f"Error modifying order in Dhan: {e}")
            return False

    def cancel_order(self, order_id: str) -> bool:
        self.ensure_valid_session()
        try:
            resp = self._client.cancel_order(order_id=str(order_id))
            return bool(isinstance(resp, dict) and resp.get("status", "").lower() == "success")
        except Exception as e:
            logger.error(f"Error canceling order in Dhan: {e}")
            return False
