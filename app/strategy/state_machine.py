"""Strategy state machine evaluating Pine Script signals on candle close and real-time tick exits."""
from datetime import datetime, date
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from app.config import settings
from app.core.logging import logger
from app.data.candle_builder import Candle, CandleBuilder
from app.execution.paper_engine import paper_engine
from app.execution.live_engine import LiveTradingEngine
from app.execution.sizing import position_sizer
from app.storage.db import db
from app.strategy.indicator import PineIndicatorState, round_to_mintick

IST = ZoneInfo("Asia/Kolkata")

class StrategyStateMachine:
    """
    Manages strategy state across tracked contracts.
    Evaluates buy signals strictly on candle close.
    Evaluates stop loss and target touches on EVERY TICK.
    """

    def __init__(
        self,
        candle_builder: CandleBuilder,
        live_engine: Optional[LiveTradingEngine] = None,
        tick_stream: Optional[Any] = None,
        adapter: Optional[Any] = None,
        on_signal_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_trade_event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ):
        self.candle_builder = candle_builder
        self.live_engine = live_engine
        self.tick_stream = tick_stream
        self.adapter = adapter
        self.on_signal_callback = on_signal_callback
        self.on_trade_event_callback = on_trade_event_callback

        # Indicator state per token: token -> PineIndicatorState
        self._indicators: Dict[str, PineIndicatorState] = {}
        # Tracking bar index of last exit per contract: token -> int
        self._last_exit_bar: Dict[str, int] = {}
        # Highest price reached since entry (for trailing stop)
        self._highest_price_since_entry: float = 0.0
        # Bar index when bought
        self._bought_bar_index: Optional[int] = None
        # Entry ATR cached for trailing stop
        self._entry_atr: float = 0.0

        # Reference to NIFTY spot token
        self.index_token: Optional[str] = None
        # Execution Mode: Hard rule - always starts in PAPER
        self.execution_mode: str = "PAPER"
        # Emergency kill-switch
        self.is_kill_switch_active: bool = False

    @property
    def current_engine(self):
        """Returns the active trading engine (paper or live)."""
        if self.execution_mode == "LIVE" and self.live_engine:
            return self.live_engine
        return paper_engine

    @property
    def active_trade(self) -> Optional[Dict[str, Any]]:
        return self.current_engine.active_trade

    def switch_mode(self, new_mode: str, confirmation: str, is_feed_healthy: bool) -> Tuple[bool, str]:
        """
        Switch between PAPER and LIVE mode with safety confirmation.
        """
        target = new_mode.upper().strip()
        if target == self.execution_mode:
            return True, f"Already in {target} mode."

        if target == "LIVE":
            if confirmation.strip() != "LIVE":
                return False, "Failed: You must type 'LIVE' to confirm switching to live execution."
            if self.active_trade:
                return False, "Failed: Cannot switch to LIVE while a position is open."
            if not is_feed_healthy:
                return False, "Failed: Cannot switch to LIVE when the market feed is stale or disconnected."
            if not self.live_engine:
                return False, "Failed: Live trading engine is not initialized."

            self.execution_mode = "LIVE"
            logger.warning("[MODE SWITCH] ACTIVE MODE CHANGED TO LIVE EXECUTION.")
            return True, "Switched to LIVE mode. Real orders will be sent to Angel One."

        elif target == "PAPER":
            if self.active_trade:
                return False, "Failed: Cannot switch to PAPER while a LIVE position is open."
            self.execution_mode = "PAPER"
            logger.info("[MODE SWITCH] Switched to PAPER mode.")
            return True, "Switched to PAPER mode."

        return False, f"Invalid mode: {new_mode}"

    def get_indicator_state(self, token: str) -> PineIndicatorState:
        token_str = str(token)
        if token_str not in self._indicators:
            self._indicators[token_str] = PineIndicatorState()
        return self._indicators[token_str]

    def on_tick(self, tick: Dict[str, Any]) -> None:
        """
        Evaluated on EVERY TICK:
        Checks stop-loss and target touches immediately.
        Updates trailing stop if enabled.
        """
        token = str(tick["token"])
        ltp = float(tick["ltp"])

        # Check if we have an open position in this contract
        active_trade = self.active_trade
        if not active_trade or str(active_trade["token"]) != token:
            return

        entry_price = active_trade["entry_price"]
        current_stop = active_trade["current_stop"]
        target = active_trade["target"]
        initial_stop = active_trade["initial_stop"]

        # Track highest price since entry
        if ltp > self._highest_price_since_entry:
            self._highest_price_since_entry = ltp

        # ---------------------------------------------------------------------
        # OPTIONAL TRAILING STOP LOGIC
        # ---------------------------------------------------------------------
        if settings.trailing_stop and self._entry_atr > 0:
            risk_1r = entry_price - initial_stop
            # Move to BE when price reaches +1R
            if ltp >= (entry_price + risk_1r):
                breakeven_sl = entry_price
                trail_sl = round_to_mintick(self._highest_price_since_entry - (settings.trailing_atr_mult * self._entry_atr))
                new_stop = max(breakeven_sl, trail_sl)
                if new_stop > current_stop:
                    self.current_engine.update_stop_loss(new_stop)
                    if self.on_trade_event_callback:
                        self.on_trade_event_callback("TRAIL_STOP", self.active_trade)

        # ---------------------------------------------------------------------
        # TICK-BY-TICK EXIT EVALUATION: STOP / TARGET TOUCHES
        # ---------------------------------------------------------------------
        hit_stop = ltp <= current_stop
        hit_target = ltp >= target

        if hit_stop or hit_target:
            reason = "SL / BOTH TOUCHED" if (hit_stop and hit_target) else ("SL TOUCHED" if hit_stop else "TARGET TOUCHED")
            logger.info(f"Tick exit condition met on {active_trade['symbol']}: {reason} (LTP: ₹{ltp:.2f})")

            # Emit Pine indicator SELL marker signal
            if self.on_signal_callback:
                self.on_signal_callback({
                    "timestamp": datetime.now(tz=IST).isoformat(),
                    "action": "SELL",
                    "token": token,
                    "symbol": active_trade["symbol"],
                    "contract_side": active_trade["option_type"],
                    "exit_price": ltp,
                    "reason": reason
                })

            closed = self.current_engine.execute_exit(tick=tick, reason=reason)
            self._highest_price_since_entry = 0.0
            self._bought_bar_index = None

            if self.on_trade_event_callback and closed:
                self.on_trade_event_callback("EXIT", closed)

    def on_candle_close(self, token: str, candle: Candle) -> None:
        """
        Evaluated the INSTANT a candle closes:
        1. Checks candle-close exit conditions if in an active position.
        2. Evaluates Pine Script buySetup if no position is open.
        """
        token_str = str(token)
        # Skip evaluating entry logic on the NIFTY spot index itself
        if self.index_token and token_str == str(self.index_token):
            return

        candles = self.candle_builder.get_history(token_str)
        if len(candles) < 25:
            return

        bar_index = len(candles) - 1
        indicator = self.get_indicator_state(token_str)

        # Resolve contract metadata for symbol and side
        from app.data.scrip_master import scrip_master
        opt_info = scrip_master.get_option_by_token(token_str)
        opt_symbol = opt_info["symbol"] if opt_info else f"NIFTY_{token_str}"
        opt_type = opt_info["option_type"] if opt_info else ("CE" if "CE" in opt_symbol else "PE")
        contract_side = 1 if opt_type == "CE" else -1

        # Fetch index candles if confirmation is needed
        index_candles = self.candle_builder.get_history(self.index_token) if (settings.useIndex and self.index_token) else None

        # Evaluate indicator
        res = indicator.evaluate(
            candles=candles,
            index_candles=index_candles,
            symbol=opt_symbol,
            contract_side=contract_side,
            strict=settings.strict,
            useIndex=settings.useIndex,
            useVWAP=settings.useVWAP,
            useVolume=settings.useVolume,
            minRVOL=settings.minRVOL,
            rewardR=settings.rewardR,
            maxRiskPct=settings.maxRiskPct,
            trendExitBars=settings.trendExitBars
        )

        active_trade = self.active_trade

        # ---------------------------------------------------------------------
        # CASE 1: ACTIVE POSITION MANAGEMENT (BAR CLOSE EXITS)
        # ---------------------------------------------------------------------
        if active_trade and str(active_trade["token"]) == token_str:
            bought_bar = self._bought_bar_index or (bar_index - 1)
            # Only evaluate bar close exits on bars strictly after bought bar
            if bar_index > bought_bar:
                entry_dt = datetime.fromisoformat(active_trade["entry_time"])
                now_dt = candle.timestamp

                new_day = now_dt.date() > entry_dt.date()
                session_exit = res.get("sessionExit", False)
                confirmed_trend_break = res.get("confirmedTrendBreak", False)
                time_limit = (bar_index - bought_bar) >= settings.maxHold

                index_reversed = False
                if settings.useIndex:
                    side = 1 if active_trade["option_type"] == "CE" else -1
                    if res.get("index_direction", 0) == -side:
                        index_reversed = True

                hit_stop = candle.low <= active_trade["current_stop"]
                hit_target = candle.high >= active_trade["target"]

                should_exit = (
                    new_day
                    or session_exit
                    or hit_stop
                    or hit_target
                    or index_reversed
                    or confirmed_trend_break
                    or time_limit
                )

                if should_exit:
                    if new_day:
                        reason = "SESSION GAP"
                    elif hit_stop:
                        reason = "SL / BOTH TOUCHED" if hit_target else "SL TOUCHED"
                    elif hit_target:
                        reason = "TARGET TOUCHED"
                    elif session_exit:
                        reason = "TIME EXIT"
                    elif index_reversed:
                        reason = "NIFTY REVERSED"
                    elif confirmed_trend_break:
                        reason = "CONFIRMED TREND EXIT"
                    else:
                        reason = "HOLD LIMIT"
                    logger.info(f"Candle close exit triggered for {active_trade['symbol']}: {reason}")

                    # Determine realistic exit reference price
                    if hit_stop:
                        sl_level = active_trade["current_stop"]
                        exit_price = min(sl_level, candle.open)
                    elif hit_target:
                        target_level = active_trade["target"]
                        exit_price = target_level
                    else:
                        exit_price = candle.close

                    # Emit Pine indicator SELL marker signal
                    if self.on_signal_callback:
                        self.on_signal_callback({
                            "timestamp": candle.timestamp.isoformat(),
                            "chart_time": int(candle.timestamp.timestamp()) + 19800,
                            "action": "SELL",
                            "token": token_str,
                            "symbol": active_trade["symbol"],
                            "contract_side": active_trade["option_type"],
                            "exit_price": exit_price,
                            "reason": reason,
                            "bar_index": bar_index
                        })

                    # Realistic exit tick fill
                    live_tick = self.tick_stream.get_latest_tick(token_str) if self.tick_stream else None
                    if live_tick and live_tick.get("best_bid", 0) > 0 and not (hit_stop or hit_target):
                        exit_tick = live_tick
                    else:
                        exit_tick = {
                            "token": token_str,
                            "ltp": exit_price,
                            "best_bid": 0.0,
                            "latency_ms": 0.0
                        }
                    closed = self.current_engine.execute_exit(tick=exit_tick, reason=reason)
                    self._last_exit_bar[token_str] = bar_index
                    self._highest_price_since_entry = 0.0
                    self._bought_bar_index = None

                    if self.on_trade_event_callback and closed:
                        self.on_trade_event_callback("EXIT", closed)
                    return

        # ---------------------------------------------------------------------
        # CASE 2: NEW ENTRY EVALUATION (PINE SCRIPT BUY SETUP)
        # ---------------------------------------------------------------------
        if not active_trade:
            # Check cooldown
            last_exit = self._last_exit_bar.get(token_str)
            cooldown_ok = (last_exit is None) or ((bar_index - last_exit) >= settings.cooldown)

            can_enter = (
                cooldown_ok
                and res.get("entryHours", False)
                and res.get("buySetup", False)
            )

            if can_enter:
                entry_price = res["close"]
                candidate_stop = res["candidateStop"]
                target = res["target"]
                # Contract metadata for symbol & lot size
                from app.data.scrip_master import scrip_master
                opt_info = scrip_master.get_option_by_token(token_str) or {
                    "symbol": f"NIFTY_{token_str}",
                    "lot_size": 65,
                    "option_type": "CE" if contract_side == 1 else "PE"
                }
                symbol = opt_info.get("symbol", f"NIFTY_{token_str}")
                opt_type = opt_info.get("option_type", "CE" if (contract_side == 1 or "CE" in symbol) else "PE")
                lot_size = opt_info.get("lot_size", 65)

                signal_record = {
                    "timestamp": candle.timestamp.isoformat(),
                    "chart_time": int(candle.timestamp.timestamp()) + 19800,
                    "action": "BUY",
                    "token": token_str,
                    "symbol": symbol,
                    "contract_side": opt_type,
                    "entry_price": entry_price,
                    "stop_loss": candidate_stop,
                    "target": target,
                    "bar_index": bar_index
                }
                db.save_signal(signal_record)
                logger.info(f"[PINE SIGNAL] BUY {opt_type} setup on {symbol} @ Rs. {entry_price:.2f} | SL: {candidate_stop:.2f} | T: {target:.2f}")

                if self.on_signal_callback:
                    self.on_signal_callback(signal_record)

                # Consult Sizing & Risk Management (Never alters or creates entry decision)
                capital = settings.PAPER_CAPITAL
                if capital <= 0 and self.adapter and self.adapter.is_logged_in():
                    try:
                        capital = self.adapter.get_funds()
                    except Exception:
                        capital = 100000.0
                if capital <= 0:
                    capital = 100000.0

                closed_trades = db.get_closed_trades(50)
                today_pnl_data = db.get_today_pnl()
                today_trade_count = db.get_today_trade_count()

                # Feed freshness and spread checks
                is_feed_stale = self.tick_stream.is_stale if self.tick_stream else False
                latest_tick = self.tick_stream.get_latest_tick(token_str) if self.tick_stream else None
                bid_ask_spread_pct = 0.0
                if latest_tick:
                    ltp_val = float(latest_tick.get("ltp", 0.0))
                    b_bid = float(latest_tick.get("best_bid", 0.0))
                    b_ask = float(latest_tick.get("best_ask", 0.0))
                    if ltp_val > 0 and b_ask > b_bid > 0:
                        bid_ask_spread_pct = ((b_ask - b_bid) / ltp_val) * 100.0

                sizing = position_sizer.evaluate_entry(
                    available_capital=capital,
                    entry_premium=entry_price,
                    stop_loss=candidate_stop,
                    lot_size=lot_size,
                    closed_trades=closed_trades,
                    today_pnl=today_pnl_data["net_pnl"],
                    today_trade_count=today_trade_count,
                    bid_ask_spread_pct=bid_ask_spread_pct,
                    is_feed_stale=is_feed_stale,
                    is_kill_switch_active=self.is_kill_switch_active
                )

                db.save_sizing_decision({
                    "timestamp": datetime.now(tz=IST).isoformat(),
                    "token": token_str,
                    "symbol": symbol,
                    "available_capital": capital,
                    "allocation_pct": sizing.get("allocation_pct", settings.BASE_ALLOCATION_PCT),
                    "budget": sizing.get("budget", 0.0),
                    "lot_size": lot_size,
                    "quantity": sizing.get("quantity", 0),
                    "reason": sizing["reason"]
                })

                if not sizing["allowed"]:
                    logger.warning(f"Trade sizing blocked entry: {sizing['reason']}")
                    return

                # Execute Entry: Use real best_ask if live depth available, otherwise enforce slippage
                live_tick = self.tick_stream.get_latest_tick(token_str) if self.tick_stream else None
                if live_tick and live_tick.get("best_ask", 0) > live_tick.get("ltp", 0):
                    entry_tick = live_tick
                else:
                    entry_tick = {
                        "token": token_str,
                        "ltp": entry_price,
                        "best_ask": 0.0,
                        "latency_ms": 0.0
                    }
                trade = self.current_engine.execute_entry(
                    token=token_str,
                    symbol=symbol,
                    option_type=opt_type,
                    quantity=sizing["quantity"],
                    tick=entry_tick,
                    stop_loss=candidate_stop,
                    target=target
                )

                self._bought_bar_index = bar_index
                self._highest_price_since_entry = entry_price
                self._entry_atr = res.get("atr", 0.0)

                if self.on_trade_event_callback and trade:
                    self.on_trade_event_callback("ENTRY", trade)

    def calculate_historical_markers(self, token: str, candles: List[Candle]) -> List[Dict[str, Any]]:
        """
        Replays the Pine Script state machine over historical candles to compute
        historical indicator BUY/SELL markers and real bot fills.
        """
        token_str = str(token)
        if len(candles) < 26:
            return []

        from app.data.scrip_master import scrip_master
        opt_info = scrip_master.get_option_by_token(token_str)
        opt_symbol = opt_info["symbol"] if opt_info else f"NIFTY_{token_str}"
        opt_type = opt_info["option_type"] if opt_info else ("CE" if "CE" in opt_symbol else "PE")
        contract_side = 1 if opt_type == "CE" else -1

        index_candles = self.candle_builder.get_history(self.index_token) if (settings.useIndex and self.index_token) else None

        hist_state = PineIndicatorState(mintick=0.05)
        markers: List[Dict[str, Any]] = []

        is_active = False
        bought_bar = -1
        last_exit_bar = -999
        entry = 0.0
        stop = 0.0
        target = 0.0

        n = len(candles)
        for i in range(25, n):
            sub_candles = candles[: i + 1]
            sub_index = index_candles[: i + 1] if index_candles else None

            res = hist_state.evaluate(
                candles=sub_candles,
                index_candles=sub_index,
                symbol=opt_symbol,
                contract_side=contract_side,
                strict=settings.strict,
                useIndex=settings.useIndex,
                useVWAP=settings.useVWAP,
                useVolume=settings.useVolume,
                minRVOL=settings.minRVOL,
                rewardR=settings.rewardR,
                maxRiskPct=settings.maxRiskPct,
                trendExitBars=settings.trendExitBars
            )

            bar = candles[i]
            time_val = bar.to_dict()["time"]
            bar_index = i
            exited_this_bar = False

            if is_active and bar_index > bought_bar:
                prev_bar = candles[i - 1]
                new_day = bar.timestamp.date() != prev_bar.timestamp.date()
                session_exit = res.get("sessionExit", False)
                hit_stop = bar.low <= stop
                hit_target = bar.high >= target

                index_reversed = False
                if settings.useIndex and res.get("index_direction") != 0:
                    index_reversed = res["index_direction"] == -contract_side

                time_limit = (bar_index - bought_bar) >= settings.maxHold
                confirmed_trend_break = res.get("confirmedTrendBreak", False)

                should_exit = (
                    new_day
                    or session_exit
                    or hit_stop
                    or hit_target
                    or index_reversed
                    or confirmed_trend_break
                    or time_limit
                )

                if should_exit:
                    if new_day:
                        reason = "SESSION GAP"
                    elif hit_stop:
                        reason = "SL / BOTH TOUCHED" if hit_target else "SL TOUCHED"
                    elif hit_target:
                        reason = "TARGET TOUCHED"
                    elif session_exit:
                        reason = "TIME EXIT"
                    elif index_reversed:
                        reason = "NIFTY REVERSED"
                    elif confirmed_trend_break:
                        reason = "CONFIRMED TREND EXIT"
                    else:
                        reason = "HOLD LIMIT"

                    color = "#ef4444" if hit_stop else ("#0891b2" if hit_target else "#64748b")
                    if hit_stop:
                        short_text = "SL EXIT"
                    elif hit_target:
                        short_text = "TARGET"
                    elif session_exit:
                        short_text = "TIME EXIT"
                    elif confirmed_trend_break:
                        short_text = "TREND EXIT"
                    elif time_limit:
                        short_text = "HOLD EXIT"
                    else:
                        short_text = "SELL EXIT"

                    markers.append({
                        "time": time_val,
                        "position": "aboveBar",
                        "shape": "arrowDown",
                        "color": color,
                        "text": short_text,
                        "details": f"SELL {opt_type} ({reason}) @ ₹{bar.close:.2f}"
                    })

                    is_active = False
                    exited_this_bar = True
                    last_exit_bar = bar_index
                    entry = 0.0
                    stop = 0.0
                    target = 0.0

            cooldown_ok = (last_exit_bar < 0) or ((bar_index - last_exit_bar) >= settings.cooldown)
            can_enter = (
                not is_active
                and not exited_this_bar
                and res.get("entryHours", False)
                and cooldown_ok
                and res.get("buySetup", False)
            )

            if can_enter:
                is_active = True
                bought_bar = bar_index
                entry = res["close"]
                stop = res["candidateStop"]
                target = res["target"]

                color = "#d946ef" if opt_type == "PE" else "#089981"
                markers.append({
                    "time": time_val,
                    "position": "belowBar",
                    "shape": "arrowUp",
                    "color": color,
                    "text": f"BUY {opt_type}",
                    "details": f"BUY {opt_type} @ ₹{entry:.2f} | SL: ₹{stop:.2f} | Target: ₹{target:.2f}"
                })

        # Real bot execution fills from database
        try:
            closed_trades = db.get_closed_trades(limit=100)
            for t in closed_trades:
                if str(t.get("token")) == token_str:
                    e_time = t.get("entry_time")
                    if e_time:
                        try:
                            e_dt = datetime.fromisoformat(e_time)
                            e_bucket = self.candle_builder.calculate_bucket_start(e_dt)
                            markers.append({
                                "time": int(e_bucket.timestamp()) + 19800,
                                "position": "belowBar",
                                "shape": "circle",
                                "color": "#000000",
                                "text": "BOT BUY",
                                "details": f"BOT BOUGHT @ ₹{float(t['entry_price']):.2f} (Qty: {t['quantity']})"
                            })
                        except Exception:
                            pass
                    x_time = t.get("exit_time")
                    if x_time:
                        try:
                            x_dt = datetime.fromisoformat(x_time)
                            x_bucket = self.candle_builder.calculate_bucket_start(x_dt)
                            pnl = float(t.get("net_pnl", 0.0))
                            pnl_str = f"+₹{pnl:.1f}" if pnl >= 0 else f"-₹{abs(pnl):.1f}"
                            markers.append({
                                "time": int(x_bucket.timestamp()) + 19800,
                                "position": "aboveBar",
                                "shape": "circle",
                                "color": "#000000",
                                "text": "BOT SOLD",
                                "details": f"BOT SOLD @ ₹{float(t['exit_price']):.2f} (Net: {pnl_str})"
                            })
                        except Exception:
                            pass

            active_t = self.active_trade
            if active_t and str(active_t.get("token")) == token_str:
                e_time = active_t.get("entry_time")
                if e_time:
                    try:
                        e_dt = datetime.fromisoformat(e_time)
                        e_bucket = self.candle_builder.calculate_bucket_start(e_dt)
                        markers.append({
                            "time": int(e_bucket.timestamp()) + 19800,
                            "position": "belowBar",
                            "shape": "circle",
                            "color": "#000000",
                            "text": "BOT BUY (ACTIVE)",
                            "details": f"BOT BOUGHT @ ₹{float(active_t['entry_price']):.2f} (Qty: {active_t.get('quantity', 0)})"
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Error fetching trade markers from DB: {e}")

        # Deduplicate and sort chronologically
        markers.sort(key=lambda m: m["time"])
        return markers
