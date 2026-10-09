"""Angel One Scrip Master management, instrument parsing, weekly expiry resolution, and ATM strike detection."""
import json
import os
import shutil
from datetime import datetime, date, time as dtime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import requests
from app.config import settings
from app.core.exceptions import ScripMasterError, SymbolRejectedError
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")

PRIMARY_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
FALLBACK_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
DHAN_PRIMARY_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

class ScripMaster:
    """Manages Broker Scrip Master data with local caching and NIFTY resolution."""

    def __init__(self, cache_path: Optional[Path] = None):
        if cache_path:
            self.cache_path = cache_path
        elif settings.BROKER == "DHAN":
            self.cache_path = settings.DATA_DIR / "DhanScripMaster.csv"
        else:
            self.cache_path = settings.SCRIP_MASTER_PATH
        self._raw_scrips: List[Dict[str, Any]] = []
        self._nifty_index_info: Optional[Dict[str, Any]] = None
        self._nifty_options: List[Dict[str, Any]] = []
        self._is_loaded = False

    def validate_nifty_symbol(self, symbol: str) -> bool:
        """Validate that symbol strictly belongs to NIFTY index or NIFTY options."""
        settings.validate_symbol_strictly(symbol)
        return True

    def is_cache_valid(self) -> bool:
        """Check if local cache exists and was downloaded today after 08:00 IST."""
        if not self.cache_path.exists() or self.cache_path.stat().st_size < 100000:
            return False

        mtime = datetime.fromtimestamp(self.cache_path.stat().st_mtime, tz=IST)
        now = datetime.now(tz=IST)

        if mtime.date() != now.date():
            return False

        # If today passed 08:00 IST and file was downloaded before 08:00 IST, consider stale
        refresh_cutoff = now.replace(hour=8, minute=0, second=0, microsecond=0)
        if now >= refresh_cutoff and mtime < refresh_cutoff:
            return False

        return True

    def download_scrip_master(self, force: bool = False) -> Path:
        """Download scrip master and save locally with streaming."""
        if not force and self.is_cache_valid():
            logger.info(f"Using existing cached Scrip Master at {self.cache_path}")
            return self.cache_path

        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        tmp_path = self.cache_path.with_suffix(".tmp")
        urls = [DHAN_PRIMARY_URL] if settings.BROKER == "DHAN" else [PRIMARY_URL, FALLBACK_URL]
        success = False

        for url in urls:
            try:
                logger.info(f"Downloading Scrip Master from {url}...")
                with requests.get(url, headers=headers, stream=True, timeout=60) as resp:
                    resp.raise_for_status()
                    with open(tmp_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                f.write(chunk)
                success = True
                break
            except Exception as e:
                logger.warning(f"Download failed from {url}: {e}")

        if not success or not tmp_path.exists():
            if self.cache_path.exists():
                logger.warning("Download failed; falling back to existing cached Scrip Master.")
                return self.cache_path
            raise ScripMasterError("Failed to download Scrip Master from provider URLs.")

        # Atomic replace
        if tmp_path.exists():
            shutil.move(str(tmp_path), str(self.cache_path))
            logger.info(f"Scrip Master saved successfully to {self.cache_path} ({self.cache_path.stat().st_size / (1024*1024):.2f} MB)")

        return self.cache_path

    def _load_dhan(self) -> None:
        """Parse Dhan CSV scrip master."""
        import pandas as pd
        logger.info("Parsing instruments from Dhan Scrip Master CSV...")
        df = pd.read_csv(self.cache_path, usecols=[
            'SEM_EXM_EXCH_ID', 'SEM_SEGMENT', 'SEM_SMST_SECURITY_ID',
            'SEM_INSTRUMENT_NAME', 'SEM_TRADING_SYMBOL', 'SEM_OPTION_TYPE',
            'SEM_STRIKE_PRICE', 'SEM_EXPIRY_DATE', 'SEM_LOT_UNITS', 'SEM_TICK_SIZE'
        ])

        self._nifty_index_info = {
            "token": "13",
            "symbol": "NIFTY",
            "name": "NIFTY",
            "exch_seg": "IDX_I",
            "exchange_type": 0,  # marketfeed.IDX
            "lot_size": 1,
            "tick_size": 0.05
        }

        opts = df[(df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & (df['SEM_TRADING_SYMBOL'].str.startswith('NIFTY'))]
        options = []
        for _, r in opts.iterrows():
            try:
                exp_raw = str(r['SEM_EXPIRY_DATE'])[:10]
                exp_date = datetime.strptime(exp_raw, "%Y-%m-%d").date()
                symbol = str(r['SEM_TRADING_SYMBOL']).strip()
                opt_type = str(r['SEM_OPTION_TYPE']).upper().strip()
                if opt_type not in ("CE", "PE"):
                    continue

                options.append({
                    "token": str(r['SEM_SMST_SECURITY_ID']),
                    "symbol": symbol,
                    "name": "NIFTY",
                    "exch_seg": "NSE_FNO",
                    "exchange_type": 2,  # marketfeed.NSE_FNO
                    "expiry_str": exp_date.strftime("%d%b%Y").upper(),
                    "expiry_date": exp_date,
                    "strike": float(r['SEM_STRIKE_PRICE']),
                    "option_type": opt_type,
                    "lot_size": int(r.get('SEM_LOT_UNITS', 25) or 25),
                    "tick_size": float(r.get('SEM_TICK_SIZE', 0.05) or 0.05),
                    "freeze_qty": 1800
                })
            except Exception:
                continue

        if not options:
            raise ScripMasterError("No NIFTY options found in Dhan Scrip Master.")

        self._nifty_options = options
        self._is_loaded = True
        logger.info(f"Dhan Scrip Master loaded: NIFTY Index Token=13, Options Count={len(self._nifty_options)}")

    def _load_angel(self) -> None:
        """Parse Angel One JSON scrip master."""
        logger.info("Parsing instruments from Scrip Master JSON...")
        with open(self.cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ScripMasterError("Invalid Scrip Master JSON format: root is not a list.")

        self._raw_scrips = data

        # 1. Resolve NIFTY Index instrument
        nifty_spot = None
        for item in self._raw_scrips:
            if item.get("exch_seg") == "NSE" and item.get("name") == "NIFTY":
                if item.get("token") == "99926000":
                    nifty_spot = item
                    break
                elif not nifty_spot:
                    nifty_spot = item

        if not nifty_spot:
            nifty_spot = {
                "token": "99926000",
                "symbol": "Nifty 50",
                "name": "NIFTY",
                "exch_seg": "NSE",
                "lotsize": "1",
                "tick_size": "5.000000"
            }
            logger.warning("NIFTY spot not found in master; using standard token 99926000.")

        raw_index_tick = float(nifty_spot.get("tick_size", 5.0))
        index_tick = (raw_index_tick / 100.0) if raw_index_tick >= 1.0 else raw_index_tick
        if index_tick <= 0.0:
            index_tick = 0.05

        self._nifty_index_info = {
            "token": nifty_spot["token"],
            "symbol": nifty_spot.get("symbol", "Nifty 50"),
            "name": "NIFTY",
            "exch_seg": "NSE",
            "exchange_type": 1,
            "lot_size": int(nifty_spot.get("lotsize", 1)),
            "tick_size": index_tick
        }

        options = []
        for item in self._raw_scrips:
            if (
                item.get("exch_seg") == "NFO"
                and item.get("name") == "NIFTY"
                and item.get("instrumenttype") == "OPTIDX"
            ):
                try:
                    raw_strike = float(item.get("strike", 0.0))
                    strike = raw_strike / 100.0
                    lot_size = int(item.get("lotsize", 65))
                    raw_tick = float(item.get("tick_size", 5.0))
                    tick_size = raw_tick / 100.0 if raw_tick >= 1.0 else raw_tick
                    freeze_qty = int(item.get("freeze_qty", 3510))
                    symbol = item.get("symbol", "").strip()
                    opt_type = "CE" if symbol.endswith("CE") else ("PE" if symbol.endswith("PE") else "")
                    if not opt_type:
                        continue
                    exp_str = item.get("expiry", "").strip().upper()
                    exp_date = datetime.strptime(exp_str, "%d%b%Y").date()

                    options.append({
                        "token": item.get("token"),
                        "symbol": symbol,
                        "name": "NIFTY",
                        "exch_seg": "NFO",
                        "exchange_type": 2,
                        "expiry_str": exp_str,
                        "expiry_date": exp_date,
                        "strike": strike,
                        "option_type": opt_type,
                        "lot_size": lot_size,
                        "tick_size": tick_size,
                        "freeze_qty": freeze_qty
                    })
                except Exception:
                    continue

        if not options:
            raise ScripMasterError("No NIFTY options (OPTIDX) found in Scrip Master.")

        self._nifty_options = options
        self._is_loaded = True
        logger.info(
            f"Angel Scrip Master loaded: NIFTY Index Token={self._nifty_index_info['token']}, "
            f"NIFTY Options Count={len(self._nifty_options)}"
        )

    def load(self, force_refresh: bool = False) -> None:
        """Load and parse NIFTY instruments from cache or download."""
        if self._is_loaded and not force_refresh:
            return

        self.download_scrip_master(force=force_refresh)

        if settings.BROKER == "DHAN":
            self._load_dhan()
        else:
            self._load_angel()

    def get_nifty_index(self) -> Dict[str, Any]:
        """Get NIFTY 50 index contract information."""
        self.load()
        return self._nifty_index_info

    def get_valid_expiries(self) -> List[date]:
        """
        Get all sorted valid future expiries for NIFTY options.
        Handles expiry-day rollover after 15:30 IST.
        """
        self.load()
        now_ist = datetime.now(tz=IST)
        today = now_ist.date()

        # If today is after 15:30 IST, today's expiry is already expired/rolled over
        is_after_market = now_ist.time() >= dtime(15, 30)

        all_expiries = sorted(list(set(opt["expiry_date"] for opt in self._nifty_options)))

        valid = []
        for exp in all_expiries:
            if exp < today:
                continue
            if exp == today and is_after_market:
                continue
            valid.append(exp)

        return valid

    def get_nearest_expiry(self) -> date:
        """Get the nearest valid weekly expiry date."""
        valid_exp = self.get_valid_expiries()
        if not valid_exp:
            raise ScripMasterError("No upcoming valid NIFTY option expiries found.")
        return valid_exp[0]

    def resolve_atm_options(
        self,
        spot_price: float,
        expiry: Optional[date] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Resolve ATM Call (CE) and Put (PE) option contracts for NIFTY.
        Strike step is 50.
        Returns: (atm_ce, atm_pe)
        """
        self.load()
        target_expiry = expiry or self.get_nearest_expiry()

        # NIFTY strike step is 50
        atm_strike = round(spot_price / 50.0) * 50.0

        ce_contract = None
        pe_contract = None

        for opt in self._nifty_options:
            if opt["expiry_date"] == target_expiry and abs(opt["strike"] - atm_strike) < 0.01:
                if opt["option_type"] == "CE":
                    ce_contract = opt
                elif opt["option_type"] == "PE":
                    pe_contract = opt

        # Fallback if exact strike not found, pick closest
        if not ce_contract or not pe_contract:
            candidates = [opt for opt in self._nifty_options if opt["expiry_date"] == target_expiry]
            if candidates:
                if not ce_contract:
                    ce_candidates = [c for c in candidates if c["option_type"] == "CE"]
                    if ce_candidates:
                        ce_contract = min(ce_candidates, key=lambda c: abs(c["strike"] - atm_strike))
                if not pe_contract:
                    pe_candidates = [c for c in candidates if c["option_type"] == "PE"]
                    if pe_candidates:
                        pe_contract = min(pe_candidates, key=lambda c: abs(c["strike"] - atm_strike))

        if not ce_contract or not pe_contract:
            raise ScripMasterError(
                f"Could not resolve ATM options for spot {spot_price} and expiry {target_expiry}"
            )

        return ce_contract, pe_contract

    def get_option_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Find contract metadata by token."""
        self.load()
        for opt in self._nifty_options:
            if str(opt["token"]) == str(token):
                return opt
        return None

    def get_fixed_option(
        self,
        strike: float,
        option_type: str,
        expiry: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Find a specific NIFTY option by strike, option_type ('CE'/'PE'), and optional expiry.
        If expiry is omitted or not found, uses the nearest weekly expiry.
        """
        self.load()
        opt_type = option_type.upper().strip()
        if opt_type not in ("CE", "PE"):
            raise ScripMasterError(f"Invalid option type '{option_type}'. Must be CE or PE.")

        target_date = None
        if expiry:
            exp_clean = expiry.strip().upper()
            for fmt in ("%d%b%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y"):
                try:
                    target_date = datetime.strptime(exp_clean, fmt).date()
                    break
                except ValueError:
                    continue
            if not target_date:
                for opt in self._nifty_options:
                    if opt["expiry_str"] == exp_clean:
                        target_date = opt["expiry_date"]
                        break

        if not target_date:
            target_date = self.get_nearest_expiry()

        for opt in self._nifty_options:
            if (
                opt["expiry_date"] == target_date
                and opt["option_type"] == opt_type
                and abs(opt["strike"] - strike) < 0.01
            ):
                return opt

        raise ScripMasterError(
            f"Fixed NIFTY option not found for Strike: {strike}, Type: {opt_type}, Expiry: {target_date}"
        )

    def validate_option_symbol(self, symbol: str) -> None:
        """Strictly validate that symbol is a NIFTY option."""
        settings.validate_symbol_strictly(symbol)

# Global singleton
scrip_master = ScripMaster()
