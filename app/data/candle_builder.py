"""Real-time tick-to-candle engine aligned with exchange hours and historical bar stitching."""
from datetime import datetime, timedelta, time as dtime
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")

class Candle:
    """Represents a single OHLCV candlestick."""
    def __init__(
        self,
        timestamp: datetime,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        volume: float = 0.0,
        is_closed: bool = False,
        timeframe_minutes: int = 1
    ):
        self.timestamp = timestamp
        self.open = open_price
        self.high = high_price
        self.low = low_price
        self.close = close_price
        self.volume = volume
        self.is_closed = is_closed
        self.timeframe_minutes = timeframe_minutes

    @property
    def close_time(self) -> datetime:
        return self.timestamp + timedelta(minutes=self.timeframe_minutes)

    def update(self, ltp: float, vol_delta: float):
        if ltp > self.high:
            self.high = ltp
        if ltp < self.low:
            self.low = ltp
        self.close = ltp
        if vol_delta > 0:
            self.volume += vol_delta

    def to_dict(self) -> Dict[str, Any]:
        # TradingView Lightweight Charts operates internally in UTC.
        # Adding 19800 seconds (5h 30m IST offset) aligns candle timestamps
        # so that the time axis and crosshair tooltips display accurate Indian Standard Time (IST).
        chart_time = int(self.timestamp.timestamp()) + 19800
        return {
            "timestamp": self.timestamp.isoformat(),
            "close_time": self.close_time.isoformat(),
            "time": chart_time,
            "raw_time": int(self.timestamp.timestamp()),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
            "volume": int(self.volume),
            "is_closed": self.is_closed
        }

    def __repr__(self):
        return f"<Candle {self.timestamp.strftime('%H:%M')} O:{self.open} H:{self.high} L:{self.low} C:{self.close} V:{self.volume} Closed:{self.is_closed}>"


class CandleBuilder:
    """
    Builds candles from live ticks bucketed by exchange timestamp in IST.
    Supports 1, 3, 5, 10, 15 minute timeframes aligned with market open (09:15 IST).
    """

    def __init__(
        self,
        timeframe_minutes: int = 1,
        on_candle_close: Optional[Callable[[str, Candle], None]] = None,
        on_candle_update: Optional[Callable[[str, Candle], None]] = None
    ):
        self.timeframe_minutes = timeframe_minutes
        self.on_candle_close = on_candle_close
        self.on_candle_update = on_candle_update

        # History per token: token -> List[Candle]
        self._history: Dict[str, List[Candle]] = {}
        # Current forming candle per token: token -> Candle
        self._current_candle: Dict[str, Candle] = {}
        # Last known cumulative day volume per token: token -> int
        self._last_cum_volume: Dict[str, int] = {}
        # Cumulative volume at current candle start: token -> int
        self._bar_start_cum_vol: Dict[str, int] = {}

    def get_history(self, token: str) -> List[Candle]:
        """Return closed candle history for a token."""
        return list(self._history.get(str(token), []))

    def get_completed_candles(self, token: str) -> List[Candle]:
        """Alias for get_history returning completed closed candles."""
        return self.get_history(token)

    def get_current_candle(self, token: str) -> Optional[Candle]:
        """Return currently forming candle for a token."""
        return self._current_candle.get(str(token))

    def get_all_candles(self, token: str) -> List[Candle]:
        """Return full history plus current forming candle."""
        candles = list(self._history.get(str(token), []))
        curr = self._current_candle.get(str(token))
        if curr:
            candles.append(curr)
        return candles

    def calculate_bucket_start(self, dt: datetime) -> datetime:
        """
        Calculate bar start time aligned with NSE market open (09:15 IST).
        For 5-min: 09:15, 09:20, 09:25...
        """
        total_mins = dt.hour * 60 + dt.minute
        market_open_mins = 9 * 60 + 15  # 09:15 IST = 555 mins

        if total_mins >= market_open_mins:
            offset = total_mins - market_open_mins
            bar_start_mins = market_open_mins + (offset // self.timeframe_minutes) * self.timeframe_minutes
        else:
            # Pre-market or early ticks: bucket from midnight
            bar_start_mins = (total_mins // self.timeframe_minutes) * self.timeframe_minutes

        hour = (bar_start_mins // 60) % 24
        minute = bar_start_mins % 60
        return dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def load_historical_candles(self, token: str, raw_candles: List[Dict[str, Any]]) -> None:
        """
        Preload historical candles from API.
        raw_candles: [{'timestamp': str, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}, ...]
        """
        token_str = str(token)
        candle_list: List[Candle] = []

        for item in raw_candles:
            if isinstance(item, (list, tuple)):
                ts_str = str(item[0])
                o_val, h_val, l_val, c_val = float(item[1]), float(item[2]), float(item[3]), float(item[4])
                v_val = float(item[5]) if len(item) > 5 else 0.0
            else:
                ts_str = str(item["timestamp"])
                o_val = float(item["open"])
                h_val = float(item["high"])
                l_val = float(item["low"])
                c_val = float(item["close"])
                v_val = float(item.get("volume", 0.0))

            # Parse ISO or standard format
            try:
                if "T" in ts_str:
                    dt = datetime.fromisoformat(ts_str)
                else:
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IST)
                else:
                    dt = dt.astimezone(IST)
            except Exception:
                try:
                    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M").replace(tzinfo=IST)
                except Exception:
                    continue

            # Strict filter: Regular NSE trading session is 09:15 to 15:30 IST.
            # Discard post-market settlement bars (15:30 - 15:40) and pre-market bars.
            if dt.time() < dtime(9, 15) or dt.time() >= dtime(15, 30):
                continue

            c = Candle(
                timestamp=dt,
                open_price=o_val,
                high_price=h_val,
                low_price=l_val,
                close_price=c_val,
                volume=v_val,
                is_closed=True,
                timeframe_minutes=self.timeframe_minutes
            )
            candle_list.append(c)

        # Sort by timestamp
        candle_list.sort(key=lambda x: x.timestamp)
        self._history[token_str] = candle_list
        logger.info(f"Loaded {len(candle_list)} historical candles for token {token_str}")

    def on_tick(self, tick: Dict[str, Any]) -> None:
        """Process incoming live tick and update forming candle or close bar."""
        token = str(tick["token"])
        ltp = float(tick["ltp"])
        if ltp <= 0:
            return

        tick_dt: datetime = tick.get("datetime_ist") or datetime.now(tz=IST)

        # Enforce market hours (09:15 to 15:30 IST): ignore post-market settlement ticks
        if tick_dt.time() >= dtime(15, 30) or tick_dt.time() < dtime(9, 15):
            current = self._current_candle.get(token)
            if current is not None:
                current.is_closed = True
                self._history[token].append(current)
                self._current_candle.pop(token, None)
                if self.on_candle_close:
                    try:
                        self.on_candle_close(token, current)
                    except Exception as e:
                        logger.error(f"Error in on_candle_close callback at market close: {e}")
            return

        cum_volume = int(tick.get("volume", 0))

        bucket_start = self.calculate_bucket_start(tick_dt)
        current = self._current_candle.get(token)

        if token not in self._history:
            self._history[token] = []

        # Check if new candle has formed
        if current is None or bucket_start > current.timestamp:
            if current is not None:
                # Close the completed candle
                current.is_closed = True
                self._history[token].append(current)
                # Keep history within reasonable window (e.g. 500 bars)
                if len(self._history[token]) > 1000:
                    self._history[token] = self._history[token][-600:]

                # Fire candle close callback
                if self.on_candle_close:
                    try:
                        self.on_candle_close(token, current)
                    except Exception as e:
                        logger.error(f"Error in on_candle_close callback: {e}")

            # Open new candle
            last_vol = self._last_cum_volume.get(token, cum_volume)
            vol_delta = max(0, cum_volume - last_vol) if cum_volume >= last_vol else 0

            new_candle = Candle(
                timestamp=bucket_start,
                open_price=ltp,
                high_price=ltp,
                low_price=ltp,
                close_price=ltp,
                volume=vol_delta,
                is_closed=False,
                timeframe_minutes=self.timeframe_minutes
            )
            self._current_candle[token] = new_candle
            self._bar_start_cum_vol[token] = cum_volume
            self._last_cum_volume[token] = cum_volume

            if self.on_candle_update:
                try:
                    self.on_candle_update(token, new_candle)
                except Exception as e:
                    logger.error(f"Error in on_candle_update callback: {e}")

        else:
            # Update existing candle
            last_vol = self._last_cum_volume.get(token, cum_volume)
            vol_delta = max(0, cum_volume - last_vol) if cum_volume >= last_vol else 0
            self._last_cum_volume[token] = cum_volume

            current.update(ltp, vol_delta)

            if self.on_candle_update:
                try:
                    self.on_candle_update(token, current)
                except Exception as e:
                    logger.error(f"Error in on_candle_update callback: {e}")
