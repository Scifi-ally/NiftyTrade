"""Custom exception definitions for NiftyTrades."""

class TradingSystemError(Exception):
    """Base exception for all trading system errors."""
    pass

class AuthenticationError(TradingSystemError):
    """Raised when authentication with the broker fails."""
    pass

class SymbolRejectedError(TradingSystemError):
    """Raised when any symbol other than NIFTY index or NIFTY options is encountered."""
    pass

class FeedStaleError(TradingSystemError):
    """Raised when the real-time data feed stops receiving ticks."""
    pass

class ScripMasterError(TradingSystemError):
    """Raised when scrip master download or parsing fails."""
    pass

class HistoricalDataError(TradingSystemError):
    """Raised when fetching historical candles fails."""
    pass

class OrderExecutionError(TradingSystemError):
    """Raised when an order placement, modification, or cancellation fails."""
    pass

class InsufficientFundsError(TradingSystemError):
    """Raised when account capital cannot afford the required lot size."""
    pass

class ConfigurationError(TradingSystemError):
    """Raised when application configuration is missing or invalid."""
    pass
