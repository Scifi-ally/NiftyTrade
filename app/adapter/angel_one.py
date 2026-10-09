"""Angel One SmartAPI adapter implementing BrokerAdapter with auto TOTP, rate limiting, and backoff."""
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import pyotp
from SmartApi.smartConnect import SmartConnect
from app.adapter.base import BrokerAdapter
from app.config import settings
from app.core.exceptions import AuthenticationError, HistoricalDataError, OrderExecutionError
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")

class AngelOneAdapter(BrokerAdapter):
    """Adapter for Angel One SmartAPI using SmartConnect and pyotp."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_code: Optional[str] = None,
        pin: Optional[str] = None,
        totp_secret: Optional[str] = None
    ):
        self.api_key = api_key or settings.ANGEL_API_KEY
        self.client_code = client_code or settings.ANGEL_CLIENT_CODE
        self.pin = pin or settings.ANGEL_PIN
        self.totp_secret = totp_secret or settings.ANGEL_TOTP_SECRET

        self._smart_connect: Optional[SmartConnect] = None
        self._jwt_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._last_login_time: Optional[datetime] = None
        self._is_logged_in: bool = False
        self._last_request_time: float = 0.0

    def _throttle(self, min_interval: float = 0.4) -> None:
        """Enforce minimum interval between Angel One REST API requests to comply with broker rate limits."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    def login(self, max_retries: int = 3, retry_delay: float = 2.0) -> bool:
        """Authenticate with Angel One SmartAPI using auto-generated TOTP."""
        if not all([self.api_key, self.client_code, self.pin, self.totp_secret]):
            missing = []
            if not self.api_key: missing.append("ANGEL_API_KEY")
            if not self.client_code: missing.append("ANGEL_CLIENT_CODE")
            if not self.pin: missing.append("ANGEL_PIN")
            if not self.totp_secret: missing.append("ANGEL_TOTP_SECRET")
            raise AuthenticationError(
                f"Missing required Angel One credentials in configuration: {', '.join(missing)}"
            )

        logger.info(f"Authenticating with Angel One SmartAPI for Client Code: {self.client_code}...")

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # 1. Clean secret and generate current TOTP
                clean_secret = self.totp_secret.replace(" ", "").strip().upper()
                totp = pyotp.TOTP(clean_secret).now()

                # 2. Instantiate SmartConnect and throttle login calls
                self._throttle(min_interval=1.0)
                self._smart_connect = SmartConnect(api_key=self.api_key)

                # 3. Request session
                session_resp = self._smart_connect.generateSession(
                    clientCode=self.client_code,
                    password=self.pin,
                    totp=totp
                )

                if session_resp and isinstance(session_resp, dict) and session_resp.get("status") is True:
                    data = session_resp.get("data", {})
                    self._jwt_token = data.get("jwtToken")
                    self._refresh_token = data.get("refreshToken")
                    self._feed_token = self._smart_connect.getfeedToken() or data.get("feedToken")
                    self._last_login_time = datetime.now(tz=IST)
                    self._is_logged_in = True
                    logger.info("Angel One authentication successful! Session active.")
                    return True

                err_msg = session_resp.get("message", "Unknown authentication error") if isinstance(session_resp, dict) else str(session_resp)
                err_code = session_resp.get("errorcode", "") if isinstance(session_resp, dict) else ""
                last_error = f"Login rejected: {err_msg} (ErrorCode: {err_code})"
                logger.warning(f"Login attempt {attempt}/{max_retries} failed: {last_error}")

                # If rate limit exceeded during session generation, back off longer
                if "exceeding access rate" in str(err_msg).lower() or err_code in ("AB1004", "AB2001"):
                    wait_time = 3.0 * attempt
                    logger.warning(f"Angel One session rate limit triggered. Backing off {wait_time:.1f}s before retry...")
                    time.sleep(wait_time)

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Login attempt {attempt}/{max_retries} encountered exception: {e}")
                if "exceeding access rate" in str(e).lower():
                    time.sleep(3.0 * attempt)

            if attempt < max_retries:
                time.sleep(retry_delay * attempt)

        self._is_logged_in = False
        raise AuthenticationError(f"Failed to authenticate with Angel One after {max_retries} attempts: {last_error}")

    def is_logged_in(self) -> bool:
        """Check if logged in and token hasn't crossed daily expiry."""
        if not self._is_logged_in or not self._last_login_time:
            return False

        now_ist = datetime.now(tz=IST)
        # Check if login was yesterday or before 09:00 IST today
        if self._last_login_time.date() != now_ist.date():
            return False
        # If today crossed 08:50 IST and login was earlier than 08:50, need refresh
        cutoff = now_ist.replace(hour=8, minute=50, second=0, microsecond=0)
        if now_ist >= cutoff and self._last_login_time < cutoff:
            return False

        return True

    def ensure_valid_session(self) -> None:
        """Re-login automatically if session is expired or stale."""
        if not self.is_logged_in():
            logger.info("Session expired or daily refresh required. Re-logging in...")
            self.login()

    def get_feed_token(self) -> Optional[str]:
        self.ensure_valid_session()
        return self._feed_token

    def get_jwt_token(self) -> Optional[str]:
        self.ensure_valid_session()
        return self._jwt_token

    def get_funds(self) -> float:
        """Fetch real available margin / cash from Angel One RMS limit."""
        self.ensure_valid_session()
        try:
            self._throttle(min_interval=0.4)
            resp = self._smart_connect.rmsLimit()
            if resp and resp.get("status") is True:
                data = resp.get("data", {})
                # 'net' represents net available cash/margin
                net_val = data.get("net", data.get("availablecash", "0.0"))
                funds = float(net_val)
                logger.info(f"Fetched real Angel One available margin: Rs. {funds:,.2f}")
                return funds
            logger.error(f"Failed to fetch RMS limits: {resp}")
            raise AuthenticationError(f"Could not retrieve account funds: {resp.get('message', 'Unknown error')}")
        except Exception as e:
            logger.error(f"Error fetching funds from Angel One: {e}")
            raise

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch current open positions from broker."""
        self.ensure_valid_session()
        try:
            self._throttle(min_interval=0.4)
            resp = self._smart_connect.position()
            if resp and resp.get("status") is True:
                return resp.get("data", []) or []
            return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_orders(self) -> List[Dict[str, Any]]:
        """Fetch order book from broker."""
        self.ensure_valid_session()
        try:
            self._throttle(min_interval=0.4)
            resp = self._smart_connect.orderBook()
            if resp and resp.get("status") is True:
                return resp.get("data", []) or []
            return []
        except Exception as e:
            logger.error(f"Error fetching order book: {e}")
            return []

    def get_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str,
        max_retries: int = 4
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candle data from Angel One SmartAPI.
        Intervals: ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE, FIFTEEN_MINUTE, ONE_DAY
        """
        self.ensure_valid_session()

        param = {
            "exchange": exchange,
            "symboltoken": str(symbol_token),
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date
        }

        for attempt in range(1, max_retries + 1):
            try:
                self._throttle(min_interval=0.4)
                resp = self._smart_connect.getCandleData(param)
                if resp and resp.get("status") is True:
                    raw_data = resp.get("data", [])
                    # Raw format: [[timestamp_iso, open, high, low, close, volume], ...]
                    candles = []
                    for item in raw_data:
                        candles.append({
                            "timestamp": item[0],
                            "open": float(item[1]),
                            "high": float(item[2]),
                            "low": float(item[3]),
                            "close": float(item[4]),
                            "volume": float(item[5])
                        })
                    return candles

                err = resp.get("message", "Unknown error") if resp else "Empty response"
                err_lower = str(err).lower()
                err_code = resp.get("errorcode", "") if resp else ""

                if "exceeding access rate" in err_lower or "rate limit" in err_lower or err_code in ("AB1004", "AB2001"):
                    wait_time = 1.5 * attempt
                    logger.warning(
                        f"Angel One rate limit exceeded for token {symbol_token} ({err}). "
                        f"Backing off {wait_time:.1f}s before retry {attempt}/{max_retries}..."
                    )
                    time.sleep(wait_time)
                elif "access token is expired" in err_lower or "invalid token" in err_lower:
                    logger.warning("Token expired during candle fetch. Re-authenticating...")
                    time.sleep(1.0)
                    self.login()
                else:
                    logger.warning(f"Candle fetch attempt {attempt} failed: {err}")

            except Exception as e:
                err_lower = str(e).lower()
                if "exceeding access rate" in err_lower or "rate limit" in err_lower:
                    wait_time = 2.0 * attempt
                    logger.warning(f"Angel One rate limit exception on token {symbol_token}: {e}. Backing off {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    logger.warning(f"Candle fetch attempt {attempt} exception: {e}")

            time.sleep(0.5 * attempt)

        raise HistoricalDataError(f"Failed to fetch historical candles for token {symbol_token} ({from_date} to {to_date})")

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
        """Place an order with Angel One."""
        self.ensure_valid_session()

        order_params = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": str(token),
            "transactiontype": transaction_type.upper(),
            "exchange": exchange.upper(),
            "ordertype": order_type.upper(),
            "producttype": product_type.upper(),
            "duration": "DAY",
            "price": f"{price:.2f}" if price > 0 else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(quantity)
        }
        if trigger_price > 0:
            order_params["triggerprice"] = f"{trigger_price:.2f}"

        try:
            resp = self._smart_connect.placeOrderFullResponse(order_params)
            if resp and resp.get("status") is True and "data" in resp:
                order_id = resp["data"].get("orderid")
                logger.info(f"Live order placed successfully: OrderID={order_id}, Symbol={symbol}, Qty={quantity}, Price={price}")
                return str(order_id)
            err_msg = resp.get("message", "Unknown rejection") if resp else "Empty response"
            raise OrderExecutionError(f"Angel One rejected order: {err_msg}")
        except Exception as e:
            logger.error(f"Error placing order via Angel One: {e}")
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
        params = {
            "variety": "NORMAL",
            "orderid": str(order_id),
            "ordertype": order_type.upper(),
            "producttype": "INTRADAY",
            "duration": "DAY",
            "price": f"{price:.2f}",
            "quantity": str(quantity)
        }
        if trigger_price > 0:
            params["triggerprice"] = f"{trigger_price:.2f}"

        resp = self._smart_connect.modifyOrder(params)
        return bool(resp and resp.get("status") is True)

    def cancel_order(self, order_id: str) -> bool:
        self.ensure_valid_session()
        resp = self._smart_connect.cancelOrder(str(order_id), "NORMAL")
        return bool(resp and resp.get("status") is True)
