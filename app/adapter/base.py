"""BrokerAdapter abstract base class for broker-agnostic interfacing."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BrokerAdapter(ABC):
    """Abstract interface defining required broker operations."""

    @abstractmethod
    def login(self) -> bool:
        """Authenticate with the broker using credentials and auto-generated TOTP."""
        pass

    @abstractmethod
    def is_logged_in(self) -> bool:
        """Return True if session is currently active and valid."""
        pass

    @abstractmethod
    def get_funds(self) -> float:
        """Fetch available cash / margin in rupees from real account."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch current open positions from broker."""
        pass

    @abstractmethod
    def get_orders(self) -> List[Dict[str, Any]]:
        """Fetch order book from broker."""
        pass

    @abstractmethod
    def get_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch historical candle data [timestamp, open, high, low, close, volume]."""
        pass

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        token: str,
        exchange: str,
        transaction_type: str,  # BUY or SELL
        order_type: str,        # LIMIT or MARKET
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
        product_type: str = "INTRADAY"
    ) -> Optional[str]:
        """Place an order with the broker and return the order ID."""
        pass

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        quantity: int,
        price: float,
        trigger_price: float = 0.0,
        order_type: str = "LIMIT"
    ) -> bool:
        """Modify an open order."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        pass

    @abstractmethod
    def get_feed_token(self) -> Optional[str]:
        """Get feed token for WebSocket connections."""
        pass
