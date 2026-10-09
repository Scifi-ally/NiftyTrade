"""Application configuration, strategy inputs, risk limits, and environment settings."""
import os
from pathlib import Path
from typing import Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.exceptions import ConfigurationError, SymbolRejectedError

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[str(BASE_DIR / ".env"), str(BASE_DIR / ".env.local")],
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # -------------------------------------------------------------------------
    # BROKER SELECTION
    # -------------------------------------------------------------------------
    BROKER: Literal["DHAN", "ANGEL_ONE"] = Field(
        default="DHAN",
        description="Active broker: 'DHAN' or 'ANGEL_ONE'"
    )

    # -------------------------------------------------------------------------
    # DHAN SECRETS (Never log or expose)
    # -------------------------------------------------------------------------
    DHAN_CLIENT_ID: str = Field(default="", description="Dhan Client ID")
    DHAN_ACCESS_TOKEN: str = Field(default="", description="Dhan 30-day Access Token")

    # -------------------------------------------------------------------------
    # ANGEL ONE SECRETS (Never log or expose)
    # -------------------------------------------------------------------------
    ANGEL_API_KEY: str = Field(default="", description="Angel One SmartAPI Application Key")
    ANGEL_CLIENT_CODE: str = Field(default="", description="Angel One Client Code / User ID")
    ANGEL_PIN: str = Field(default="", description="Angel One MPIN")
    ANGEL_TOTP_SECRET: str = Field(default="", description="Angel One Base32 TOTP Secret Key")

    # -------------------------------------------------------------------------
    # EXECUTION MODE
    # -------------------------------------------------------------------------
    # Hard rule: App always starts in PAPER mode
    EXECUTION_MODE: Literal["PAPER", "LIVE"] = Field(
        default="PAPER",
        description="Execution mode. Always initialized as PAPER on startup."
    )
    PAPER_CAPITAL: float = Field(
        default=100000.0,
        description="Paper trading starting capital override (₹). If <= 0, uses real account margin."
    )

    # -------------------------------------------------------------------------
    # CONTRACT SELECTION (NIFTY ONLY)
    # -------------------------------------------------------------------------
    CONTRACT_MODE: Literal["auto", "fixed"] = Field(
        default="auto",
        description="'auto' tracks ATM CE and ATM PE of nearest weekly expiry. 'fixed' runs on single contract."
    )
    FIXED_EXPIRY: Optional[str] = Field(default=None, description="Expiry date string (e.g. 09NOV2026 or 2026-11-09) if fixed")
    FIXED_STRIKE: float = Field(default=0.0, description="Strike price if fixed")
    FIXED_OPT_TYPE: Literal["CE", "PE"] = Field(default="CE", description="CE or PE if fixed")

    TIMEFRAME_MINUTES: int = Field(default=1, ge=1, le=15, description="Candle timeframe in minutes (1 to 15)")

    # -------------------------------------------------------------------------
    # STRATEGY INPUTS (EXACT PINE SCRIPT DEFAULTS)
    # -------------------------------------------------------------------------
    # contract: "Auto", "CE (CALL)", "PE (PUT)"
    pine_contract: Literal["Auto", "CE (CALL)", "PE (PUT)"] = Field(default="Auto")
    strict: bool = Field(default=False, description="Strict premium filters")
    useIndex: bool = Field(default=False, description="Require NIFTY direction confirmation")
    useVWAP: bool = Field(default=False, description="Require premium above VWAP")
    useVolume: bool = Field(default=False, description="Require option volume confirmation")
    minRVOL: float = Field(default=1.0, ge=0.5, description="Minimum relative volume")
    rewardR: float = Field(default=2.0, ge=1.0, le=5.0, description="Target / risk multiple")
    maxRiskPct: float = Field(default=25.0, ge=2.0, le=50.0, description="Maximum premium risk %")
    cooldown: int = Field(default=3, ge=1, le=30, description="Cooldown bars after exit")
    maxHold: int = Field(default=18, ge=3, le=60, description="Maximum holding bars")
    trendExitBars: int = Field(default=2, ge=1, le=5, description="Confirmed trend-exit bars")

    # -------------------------------------------------------------------------
    # OPTIONAL TRAILING STOP (Default OFF per Pine script)
    # -------------------------------------------------------------------------
    trailing_stop: bool = Field(default=False, description="Move SL to BE at +1R, then trail at high - 0.8*ATR(14)")
    trailing_atr_mult: float = Field(default=0.8, description="Trailing stop ATR multiplier")

    # -------------------------------------------------------------------------
    # POSITION SIZING & RISK CAPS (BOT-MANAGED, NEVER CHANGES ENTRIES)
    # -------------------------------------------------------------------------
    BASE_ALLOCATION_PCT: float = Field(default=15.0, ge=5.0, le=20.0, description="Base budget allocation (% of fund)")
    MIN_ALLOCATION_PCT: float = Field(default=5.0, ge=1.0, le=15.0, description="Minimum adaptive allocation (% of fund)")
    MAX_ALLOCATION_PCT: float = Field(default=20.0, ge=15.0, le=25.0, description="Maximum adaptive allocation (% of fund)")

    MAX_RISK_PER_TRADE_PCT: float = Field(default=2.0, ge=0.5, le=5.0, description="Max allowed risk per trade as % of fund")
    MAX_DAILY_LOSS: float = Field(default=5000.0, ge=500.0, description="Max daily loss in ₹ before auto-halting")
    MAX_DAILY_TRADES: int = Field(default=5, ge=1, le=20, description="Max trades allowed per day")
    MAX_LOTS: int = Field(default=10, ge=1, le=50, description="Max lots per trade cap")

    # Execution buffers and safety thresholds
    MAX_SPREAD_PCT: float = Field(default=2.5, description="Max bid-ask spread % allowed for entry")
    STALE_FEED_SECONDS: int = Field(default=15, description="Seconds without tick before marking feed stale")
    SLIPPAGE_POINTS: float = Field(default=0.5, description="Simulated slippage points when depth is unavailable")
    LIMIT_BUFFER_POINTS: float = Field(default=1.0, description="Marketable limit price buffer added to ask/LTP")

    # System paths
    DATA_DIR: Path = Field(default=BASE_DIR / "data")
    DB_PATH: Path = Field(default=BASE_DIR / "data" / "nifty_trades.db")
    SCRIP_MASTER_PATH: Path = Field(default=BASE_DIR / "data" / "OpenAPIScripMaster.json")
    LOG_LEVEL: str = Field(default="INFO")

    @field_validator("EXECUTION_MODE")
    @classmethod
    def enforce_initial_paper_mode(cls, v: str) -> str:
        # Hard Rule: App ALWAYS starts in PAPER
        return "PAPER"

    def validate_symbol_strictly(self, symbol: str) -> None:
        """Reject any symbol other than NIFTY index or NIFTY options."""
        clean = symbol.strip().upper()
        if clean in ("NIFTY", "NIFTY 50", "NSE:NIFTY", "99926000", "26000", "13"):
            return
        if clean.startswith("NIFTY") and (clean.endswith("CE") or clean.endswith("PE")):
            return
        raise SymbolRejectedError(
            f"REJECTED: Symbol '{symbol}' is not permitted. Only NIFTY index and NIFTY options are supported."
        )

# Global settings instance
settings = Settings()
# Ensure data directory exists
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
