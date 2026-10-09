"""Exact mathematical port of the Pine Script indicator for NIFTY Options.

Follows TradingView documented definitions:
- EMA seeded with SMA
- Wilder RMA for RSI and ATR
- VWAP of hlc3 reset each day
- Prior high, swing low, candle range, RVOL
- All entry filters (reclaim, breakRecentHigh, pullbackResume, trendOK, momentumOK, candleOK, chaseOK, riskOK)
- Confirmed trend break counter
"""
import math
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from app.config import settings

IST = ZoneInfo("Asia/Kolkata")

def round_to_mintick(val: float, mintick: float = 0.05) -> float:
    """Round value to the nearest exchange tick size (0.05)."""
    if mintick <= 0:
        mintick = 0.05
    return round(round(val / mintick) * mintick, 2)

def calculate_sma(series: List[float], length: int) -> List[Optional[float]]:
    """Simple Moving Average."""
    result: List[Optional[float]] = []
    for i in range(len(series)):
        if i + 1 < length:
            result.append(None)
        else:
            window = series[i + 1 - length : i + 1]
            result.append(sum(window) / length)
    return result

def calculate_ema(series: List[float], length: int) -> List[Optional[float]]:
    """
    Exponential Moving Average matching TradingView ta.ema:
    Seeded with SMA of first `length` elements, then alpha = 2 / (length + 1).
    """
    result: List[Optional[float]] = []
    if len(series) < length:
        return [None] * len(series)

    # First `length - 1` are None
    for i in range(length - 1):
        result.append(None)

    # First EMA value is SMA of first `length` values
    sma_seed = sum(series[:length]) / length
    result.append(sma_seed)

    alpha = 2.0 / (length + 1.0)
    for i in range(length, len(series)):
        prev_ema = result[-1]
        curr_ema = alpha * series[i] + (1.0 - alpha) * prev_ema
        result.append(curr_ema)

    return result

def calculate_rma(series: List[float], length: int) -> List[Optional[float]]:
    """
    Wilder Moving Average (RMA) matching TradingView ta.rma:
    Seeded with SMA of first `length` elements, then alpha = 1 / length.
    """
    result: List[Optional[float]] = []
    if len(series) < length:
        return [None] * len(series)

    for i in range(length - 1):
        result.append(None)

    sma_seed = sum(series[:length]) / length
    result.append(sma_seed)

    alpha = 1.0 / float(length)
    for i in range(length, len(series)):
        prev_rma = result[-1]
        curr_rma = alpha * series[i] + (1.0 - alpha) * prev_rma
        result.append(curr_rma)

    return result

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], length: int = 14) -> List[Optional[float]]:
    """
    Average True Range matching TradingView ta.atr(14) using RMA of TR.
    TR = max(high - low, abs(high - close[1]), abs(low - close[1]))
    """
    if not highs:
        return []

    tr_series: List[float] = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_series.append(tr)

    return calculate_rma(tr_series, length)

def calculate_rsi(closes: List[float], length: int = 14) -> List[Optional[float]]:
    """
    Relative Strength Index matching TradingView ta.rsi(close, 14) using RMA.
    """
    if len(closes) < length + 1:
        return [None] * len(closes)

    changes = [0.0]
    for i in range(1, len(closes)):
        changes.append(closes[i] - closes[i - 1])

    gains = [max(c, 0.0) for c in changes[1:]]
    losses = [max(-c, 0.0) for c in changes[1:]]

    rma_gains = calculate_rma(gains, length)
    rma_losses = calculate_rma(losses, length)

    result: List[Optional[float]] = [None]  # For changes[0]
    for g, l in zip(rma_gains, rma_losses):
        if g is None or l is None:
            result.append(None)
        elif l == 0.0:
            result.append(100.0)
        elif g == 0.0:
            result.append(0.0)
        else:
            rs = g / l
            rsi_val = 100.0 - (100.0 / (1.0 + rs))
            result.append(rsi_val)

    return result

def calculate_vwap(
    timestamps: List[datetime],
    highs: List[float],
    lows: List[float],
    closes: List[float],
    volumes: List[float]
) -> List[Optional[float]]:
    """
    Volume Weighted Average Price (VWAP) of hlc3 reset each day.
    """
    result: List[Optional[float]] = []
    cum_pv = 0.0
    cum_vol = 0.0
    current_day = None

    for i in range(len(timestamps)):
        dt = timestamps[i]
        day = dt.date()
        if day != current_day:
            # Reset on new day
            current_day = day
            cum_pv = 0.0
            cum_vol = 0.0

        hlc3 = (highs[i] + lows[i] + closes[i]) / 3.0
        v = volumes[i]
        cum_pv += hlc3 * v
        cum_vol += v

        if cum_vol > 0:
            result.append(cum_pv / cum_vol)
        else:
            result.append(hlc3)

    return result


class PineIndicatorState:
    """
    Evaluates the Pine Script strategy rules bar by bar with identical logic.
    """

    def __init__(self, mintick: float = 0.05):
        self.mintick = mintick
        self.trend_break_count = 0

    def evaluate(
        self,
        candles: List[Any],  # List of Candle objects or dicts
        index_candles: Optional[List[Any]] = None,
        symbol: str = "NIFTY_OPT",
        contract_side: Optional[int] = None,  # 1 for CE, -1 for PE
        strict: Optional[bool] = None,
        useIndex: Optional[bool] = None,
        useVWAP: Optional[bool] = None,
        useVolume: Optional[bool] = None,
        minRVOL: Optional[float] = None,
        rewardR: Optional[float] = None,
        maxRiskPct: Optional[float] = None,
        trendExitBars: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Evaluate full Pine script indicators and return all series & signals.
        """
        # Load parameters from settings if not passed
        strict = settings.strict if strict is None else strict
        useIndex = settings.useIndex if useIndex is None else useIndex
        useVWAP = settings.useVWAP if useVWAP is None else useVWAP
        useVolume = settings.useVolume if useVolume is None else useVolume
        minRVOL = settings.minRVOL if minRVOL is None else minRVOL
        rewardR = settings.rewardR if rewardR is None else rewardR
        maxRiskPct = settings.maxRiskPct if maxRiskPct is None else maxRiskPct
        trendExitBars = settings.trendExitBars if trendExitBars is None else trendExitBars

        n = len(candles)
        if n == 0:
            return {"ready": False, "buySetup": False}

        # Extract series
        timestamps: List[datetime] = []
        opens: List[float] = []
        highs: List[float] = []
        lows: List[float] = []
        closes: List[float] = []
        volumes: List[float] = []

        for c in candles:
            if hasattr(c, "timestamp"):
                timestamps.append(c.timestamp)
                opens.append(c.open)
                highs.append(c.high)
                lows.append(c.low)
                closes.append(c.close)
                volumes.append(c.volume)
            else:
                timestamps.append(c["timestamp"] if isinstance(c["timestamp"], datetime) else datetime.fromisoformat(c["timestamp"]))
                opens.append(float(c["open"]))
                highs.append(float(c["high"]))
                lows.append(float(c["low"]))
                closes.append(float(c["close"]))
                volumes.append(float(c["volume"]))

        # Contract side: 1 for CE, -1 for PE
        if contract_side is None:
            if settings.pine_contract == "CE (CALL)":
                contract_side = 1
            elif settings.pine_contract == "PE (PUT)":
                contract_side = -1
            else:
                sym_upper = symbol.upper()
                if "CALL" in sym_upper or sym_upper.endswith("CE"):
                    contract_side = 1
                elif "PUT" in sym_upper or sym_upper.endswith("PE"):
                    contract_side = -1
                else:
                    contract_side = 0

        # Indicator series
        ema9 = calculate_ema(closes, 9)
        ema21 = calculate_ema(closes, 21)
        atr = calculate_atr(highs, lows, closes, 14)
        rsi = calculate_rsi(closes, 14)
        vwap = calculate_vwap(timestamps, highs, lows, closes, volumes)
        vol_sma20 = calculate_sma(volumes, 20)

        # Index confirmation evaluation
        index_direction = 0
        fresh_index = False
        if useIndex and index_candles and len(index_candles) >= 22:
            idx_closes = [c.close if hasattr(c, "close") else float(c["close"]) for c in index_candles]
            idx_fast = calculate_ema(idx_closes, 9)
            idx_slow = calculate_ema(idx_closes, 21)
            # f_index uses direction[1] from previous completed bar
            if len(idx_closes) >= 2:
                prev_c = idx_closes[-2]
                prev_fast = idx_fast[-2]
                prev_slow = idx_slow[-2]
                if prev_fast is not None and prev_slow is not None:
                    if prev_c > prev_slow and prev_fast > prev_slow:
                        index_direction = 1
                    elif prev_c < prev_slow and prev_fast < prev_slow:
                        index_direction = -1
                    else:
                        index_direction = 0
                fresh_index = True

        indexOK = not useIndex or (fresh_index and index_direction == contract_side)

        # Evaluate last bar
        i = n - 1
        bar_index = i

        ready = (
            bar_index >= 25
            and rsi[i] is not None
            and atr[i] is not None
            and atr[i] > 0
            and closes[i] > 0
            and ema9[i] is not None
            and ema21[i] is not None
            and ema9[i - 1] is not None
            and ema21[i - 1] is not None
        )

        if not ready:
            return {
                "bar_index": bar_index,
                "ready": False,
                "buySetup": False,
                "ema9": ema9[i],
                "ema21": ema21[i],
                "atr": atr[i],
                "rsi": rsi[i],
                "vwap": vwap[i]
            }

        # 1. Option volume & VWAP filters
        # relativeVolume = volume / averageVolume[1]
        prior_vol_sma = vol_sma20[i - 1] if i >= 1 else None
        relativeVolume = (volumes[i] / prior_vol_sma) if (prior_vol_sma is not None and prior_vol_sma > 0) else None

        volumeOK = not useVolume or (relativeVolume is not None and relativeVolume >= minRVOL)
        vwapOK = not useVWAP or (vwap[i] is not None and closes[i] > vwap[i])

        # 2. Entry Triggers
        # priorHigh = ta.highest(high, 3)[1] (i.e. highest of highs[i-3], highs[i-2], highs[i-1])
        priorHigh = max(highs[i - 3 : i]) if i >= 3 else highs[i - 1]
        priorHighPrev = max(highs[i - 4 : i - 1]) if i >= 4 else (highs[i - 2] if i >= 2 else priorHigh)

        # ta.crossover(close, ema21)
        reclaim = (closes[i] > ema21[i]) and (closes[i - 1] <= ema21[i - 1])

        # ta.crossover(close, priorHigh)
        breakRecentHigh = (closes[i] > priorHigh) and (closes[i - 1] <= priorHighPrev)

        # pullbackResume: low[1] <= ema9[1] and close > high[1] and close > ema9
        pullbackResume = (lows[i - 1] <= ema9[i - 1]) and (closes[i] > highs[i - 1]) and (closes[i] > ema9[i])

        trigger = reclaim or breakRecentHigh or pullbackResume

        # 3. Filters
        # trendOK = close > ema21 and ema9 > ema9[1] and (not strict or ema9 > ema21)
        trendOK = (closes[i] > ema21[i]) and (ema9[i] > ema9[i - 1]) and (not strict or ema9[i] > ema21[i])

        # momentumOK = rsi >= (strict ? 55 : 50) and rsi <= (strict ? 70 : 78)
        rsi_min = 55.0 if strict else 50.0
        rsi_max = 70.0 if strict else 78.0
        momentumOK = (rsi[i] >= rsi_min) and (rsi[i] <= rsi_max)

        # candleOK:
        # close > open and (close - open) / candleRange >= (strict ? 0.35 : 0.20) and
        # (close - low) / candleRange >= (strict ? 0.65 : 0.55) and
        # candleRange <= atr * (strict ? 1.8 : 2.5)
        candleRange = max(highs[i] - lows[i], self.mintick)
        body_ratio_min = 0.35 if strict else 0.20
        close_low_ratio_min = 0.65 if strict else 0.55
        range_atr_max = 1.8 if strict else 2.5

        candleOK = (
            closes[i] > opens[i]
            and ((closes[i] - opens[i]) / candleRange) >= body_ratio_min
            and ((closes[i] - lows[i]) / candleRange) >= close_low_ratio_min
            and candleRange <= (atr[i] * range_atr_max)
        )

        # chaseOK = close - ema9 <= atr * (strict ? 1.3 : 2.0)
        chase_atr_mult = 1.3 if strict else 2.0
        chaseOK = (closes[i] - ema9[i]) <= (atr[i] * chase_atr_mult)

        # 4. Stop & Risk
        # swingLow = ta.lowest(low, 3) (lowest of lows[i-2], lows[i-1], lows[i])
        swingLow = min(lows[max(0, i - 2) : i + 1])
        candidateStop = round_to_mintick(min(swingLow - atr[i] * 0.1, closes[i] - atr[i] * 0.8), self.mintick)
        candidateRisk = closes[i] - candidateStop

        riskOK = (
            candidateStop > 0
            and candidateRisk > 0
            and candidateRisk <= atr[i] * 2.5
            and ((candidateRisk / max(closes[i], self.mintick)) * 100.0) <= maxRiskPct
        )

        # 5. Session Entry Window: 09:30 - 14:45 IST
        # In Pine Script: clock = hour(time_close, tz) * 60 + minute(time_close, tz)
        close_dt = getattr(candles[i], "close_time", None)
        if not close_dt:
            tf_mins = getattr(candles[i], "timeframe_minutes", settings.TIMEFRAME_MINUTES)
            close_dt = timestamps[i] + timedelta(minutes=tf_mins)
        clock = close_dt.hour * 60 + close_dt.minute
        sessionOpen = (close_dt.weekday() < 5) and (dtime(9, 15) <= close_dt.time() <= dtime(15, 30))
        entryHours = sessionOpen and (570 <= clock < 885)
        sessionExit = (not sessionOpen) or (clock >= 915)

        # 6. Overall Buy Setup
        buySetup = (
            ready
            and trendOK
            and momentumOK
            and candleOK
            and chaseOK
            and trigger
            and riskOK
            and indexOK
            and vwapOK
            and volumeOK
        )

        # 7. Trend Broken check for exit
        trendBrokenNow = (closes[i] < ema21[i]) and (ema9[i] < ema21[i])
        if trendBrokenNow:
            self.trend_break_count += 1
        else:
            self.trend_break_count = 0
        confirmedTrendBreak = self.trend_break_count >= trendExitBars

        target = round_to_mintick(closes[i] + (closes[i] - candidateStop) * rewardR, self.mintick)

        return {
            "bar_index": bar_index,
            "timestamp": timestamps[i],
            "ready": ready,
            "buySetup": buySetup,
            "entryHours": entryHours,
            "sessionExit": sessionExit,
            "confirmedTrendBreak": confirmedTrendBreak,
            "index_direction": index_direction,
            "close": closes[i],
            "candidateStop": candidateStop,
            "candidateRisk": candidateRisk,
            "target": target,
            "ema9": ema9[i],
            "ema21": ema21[i],
            "atr": atr[i],
            "rsi": rsi[i],
            "vwap": vwap[i],
            "filters": {
                "trendOK": trendOK,
                "momentumOK": momentumOK,
                "candleOK": candleOK,
                "chaseOK": chaseOK,
                "trigger": trigger,
                "reclaim": reclaim,
                "breakRecentHigh": breakRecentHigh,
                "pullbackResume": pullbackResume,
                "riskOK": riskOK,
                "indexOK": indexOK,
                "vwapOK": vwapOK,
                "volumeOK": volumeOK
            }
        }
