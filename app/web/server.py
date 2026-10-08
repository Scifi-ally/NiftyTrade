"""FastAPI web server and real-time WebSocket hub for NiftyTrades."""
import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.adapter.angel_one import AngelOneAdapter
from app.config import settings
from app.core.exceptions import AuthenticationError
from app.core.logging import logger
from app.data.candle_builder import Candle, CandleBuilder
from app.data.scrip_master import scrip_master
from app.data.tick_stream import TickStream
from app.execution.live_engine import LiveTradingEngine
from app.execution.paper_engine import paper_engine
from app.storage.db import db
from app.strategy.state_machine import StrategyStateMachine

IST = ZoneInfo("Asia/Kolkata")
STATIC_DIR = Path(__file__).resolve().parent / "static"

class SystemState:
    """Singleton holding running system components."""
    adapter: Optional[AngelOneAdapter] = None
    tick_stream: Optional[TickStream] = None
    candle_builder: Optional[CandleBuilder] = None
    state_machine: Optional[StrategyStateMachine] = None
    live_engine: Optional[LiveTradingEngine] = None

    # Tracked contracts
    nifty_spot_info: Optional[Dict[str, Any]] = None
    atm_ce_info: Optional[Dict[str, Any]] = None
    atm_pe_info: Optional[Dict[str, Any]] = None
    active_chart_token: Optional[str] = None
    is_initialized: bool = False

system_state = SystemState()

# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            if d in self.active_connections:
                self.active_connections.remove(d)

manager = ConnectionManager()

def broadcast_sync(message: Dict[str, Any]):
    """Helper to broadcast messages to WebSockets from sync callbacks."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
    except Exception:
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Initializing NiftyTrades Automated Trading System...")
    try:
        # 1. Initialize Adapter
        system_state.adapter = AngelOneAdapter()
        if settings.ANGEL_API_KEY and settings.ANGEL_CLIENT_CODE:
            try:
                system_state.adapter.login()
                logger.info("Angel One broker session active.")
            except Exception as e:
                logger.error(f"Login failed on startup: {e}. System running in stand-by.")
        else:
            logger.warning("No credentials found in .env; please configure .env.")

        # 2. Scrip Master & Contract Resolution
        scrip_master.load()
        system_state.nifty_spot_info = scrip_master.get_nifty_index()
        nearest_expiry = scrip_master.get_nearest_expiry()

        # Resolve ATM options using reference spot or last candle
        ref_spot = 25000.0
        if system_state.adapter and system_state.adapter.is_logged_in():
            try:
                now_dt = datetime.now(tz=IST)
                from_dt_str = (now_dt - timedelta(days=5)).strftime("%Y-%m-%d 09:15")
                to_dt_str = now_dt.strftime("%Y-%m-%d 15:30")
                candles = system_state.adapter.get_candles(
                    exchange="NSE",
                    symbol_token=system_state.nifty_spot_info["token"],
                    interval="FIVE_MINUTE",
                    from_date=from_dt_str,
                    to_date=to_dt_str
                )
                if candles:
                    ref_spot = candles[-1]["close"]
            except Exception as e:
                logger.warning(f"Could not fetch spot candle for ATM resolution: {e}")

        ce, pe = scrip_master.resolve_atm_options(spot_price=ref_spot, expiry=nearest_expiry)
        system_state.atm_ce_info = ce
        system_state.atm_pe_info = pe
        system_state.active_chart_token = str(ce["token"])

        logger.info(f"Resolved ATM CE: {ce['symbol']} (Token {ce['token']}) | PE: {pe['symbol']} (Token {pe['token']})")

        # 3. Engines & State Machine
        system_state.live_engine = LiveTradingEngine(system_state.adapter)

        def on_candle_close(token: str, candle: Candle):
            broadcast_sync({
                "type": "CANDLE_CLOSE",
                "token": token,
                "candle": candle.to_dict()
            })
            if system_state.state_machine:
                system_state.state_machine.on_candle_close(token, candle)

        def on_candle_update(token: str, candle: Candle):
            broadcast_sync({
                "type": "CANDLE_UPDATE",
                "token": token,
                "candle": candle.to_dict()
            })

        system_state.candle_builder = CandleBuilder(
            timeframe_minutes=settings.TIMEFRAME_MINUTES,
            on_candle_close=on_candle_close,
            on_candle_update=on_candle_update
        )

        def on_signal(signal: Dict[str, Any]):
            broadcast_sync({"type": "SIGNAL", "signal": signal})

        def on_trade_event(event_type: str, trade: Dict[str, Any]):
            broadcast_sync({"type": "TRADE_EVENT", "event": event_type, "trade": trade})

        system_state.state_machine = StrategyStateMachine(
            candle_builder=system_state.candle_builder,
            live_engine=system_state.live_engine,
            on_signal_callback=on_signal,
            on_trade_event_callback=on_trade_event
        )
        system_state.state_machine.index_token = str(system_state.nifty_spot_info["token"])

        # 4. Historical Warm-up (200 bars)
        if system_state.adapter and system_state.adapter.is_logged_in():
            tokens_to_warm = [
                ("NSE", system_state.nifty_spot_info["token"]),
                ("NFO", system_state.atm_ce_info["token"]),
                ("NFO", system_state.atm_pe_info["token"])
            ]
            now_dt = datetime.now(tz=IST)
            from_str = (now_dt - timedelta(days=20)).strftime("%Y-%m-%d 09:15")
            to_str = now_dt.strftime("%Y-%m-%d 15:30")

            for exch, tok in tokens_to_warm:
                try:
                    c_data = system_state.adapter.get_candles(
                        exchange=exch,
                        symbol_token=str(tok),
                        interval="FIVE_MINUTE",
                        from_date=from_str,
                        to_date=to_str
                    )
                    system_state.candle_builder.load_historical_candles(str(tok), c_data)
                except Exception as e:
                    logger.warning(f"Could not warm up token {tok}: {e}")

        # 5. Live Tick Stream
        def on_tick(tick: Dict[str, Any]):
            # 1. Update CandleBuilder
            system_state.candle_builder.on_tick(tick)
            # 2. Check tick exits in state machine
            system_state.state_machine.on_tick(tick)

            # 3. Broadcast tick info & PnL if active chart token matches
            tok = str(tick["token"])
            active_trade = system_state.state_machine.active_trade
            unrealised = 0.0
            if active_trade and str(active_trade["token"]) == tok:
                pnl_calc = paper_engine.calculate_unrealised_pnl(tick["ltp"])
                unrealised = pnl_calc["unrealised_net"]

            today_pnl = db.get_today_pnl()
            stats = system_state.tick_stream.get_stats() if system_state.tick_stream else {}

            broadcast_sync({
                "type": "TICK",
                "token": tok,
                "ltp": tick["ltp"],
                "best_bid": tick["best_bid"],
                "best_ask": tick["best_ask"],
                "latency_ms": tick["latency_ms"],
                "unrealised_pnl": unrealised,
                "realised_pnl": today_pnl["net_pnl"],
                "total_pnl": round(today_pnl["net_pnl"] + unrealised, 2),
                "is_feed_stale": stats.get("is_stale", False)
            })

        if system_state.adapter and system_state.adapter.is_logged_in():
            system_state.tick_stream = TickStream(adapter=system_state.adapter, on_tick=on_tick)
            system_state.tick_stream.start()
            # Subscribe to NIFTY spot + ATM CE & PE
            system_state.tick_stream.subscribe([
                {"exchangeType": system_state.nifty_spot_info["exchange_type"], "tokens": [str(system_state.nifty_spot_info["token"])]},
                {"exchangeType": system_state.atm_ce_info["exchange_type"], "tokens": [str(system_state.atm_ce_info["token"])]},
                {"exchangeType": system_state.atm_pe_info["exchange_type"], "tokens": [str(system_state.atm_pe_info["token"])]}
            ])

        system_state.is_initialized = True
        logger.info("NiftyTrades trading system successfully started.")

    except Exception as e:
        logger.error(f"Error during system startup: {e}")

    yield

    # Shutdown
    if system_state.tick_stream:
        system_state.tick_stream.stop()
    logger.info("NiftyTrades trading system stopped.")


app = FastAPI(title="NiftyTrades", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def get_index():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/api/status")
def get_system_status():
    today_pnl = db.get_today_pnl()
    active_trade = system_state.state_machine.active_trade if system_state.state_machine else None
    stats = system_state.tick_stream.get_stats() if system_state.tick_stream else {"is_connected": False, "is_stale": True, "avg_latency_ms": 0}

    # Available funds
    funds = settings.PAPER_CAPITAL
    if system_state.adapter and system_state.adapter.is_logged_in():
        try:
            real_funds = system_state.adapter.get_funds()
            if settings.PAPER_CAPITAL <= 0:
                funds = real_funds
        except Exception:
            pass

    return {
        "execution_mode": system_state.state_machine.execution_mode if system_state.state_machine else "PAPER",
        "is_kill_switch_active": system_state.state_machine.is_kill_switch_active if system_state.state_machine else False,
        "feed_connected": stats.get("is_connected", False),
        "feed_stale": stats.get("is_stale", True),
        "avg_latency_ms": stats.get("avg_latency_ms", 0.0),
        "funds": funds,
        "active_trade": active_trade,
        "today_pnl": today_pnl,
        "active_chart_token": system_state.active_chart_token,
        "contracts": {
            "spot": system_state.nifty_spot_info,
            "atm_ce": system_state.atm_ce_info,
            "atm_pe": system_state.atm_pe_info
        }
    }

@app.get("/api/candles")
def get_candles(token: Optional[str] = None):
    tok = token or system_state.active_chart_token
    if not tok or not system_state.candle_builder:
        return {"candles": []}

    candles = system_state.candle_builder.get_all_candles(str(tok))
    return {
        "token": tok,
        "candles": [c.to_dict() for c in candles]
    }

class ModeSwitchRequest(BaseModel):
    mode: str
    confirmation: str = ""

@app.post("/api/mode")
def set_execution_mode(req: ModeSwitchRequest):
    if not system_state.state_machine:
        raise HTTPException(status_code=500, detail="State machine not initialized")

    is_feed_healthy = not (system_state.tick_stream.is_stale if system_state.tick_stream else True)
    success, msg = system_state.state_machine.switch_mode(
        new_mode=req.mode,
        confirmation=req.confirmation,
        is_feed_healthy=is_feed_healthy
    )
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    broadcast_sync({
        "type": "MODE_CHANGE",
        "mode": system_state.state_machine.execution_mode
    })
    return {"status": "SUCCESS", "mode": system_state.state_machine.execution_mode, "message": msg}

@app.post("/api/kill-switch")
def toggle_kill_switch():
    if not system_state.state_machine:
        raise HTTPException(status_code=500, detail="State machine not initialized")

    curr = system_state.state_machine.is_kill_switch_active
    system_state.state_machine.is_kill_switch_active = not curr
    status_str = "ACTIVATED" if not curr else "DEACTIVATED"

    # If activated and a trade is open, force square-off immediately
    if not curr and system_state.state_machine.active_trade:
        trade = system_state.state_machine.active_trade
        exit_tick = {
            "token": trade["token"],
            "ltp": trade["entry_price"],
            "latency_ms": 0.0
        }
        system_state.state_machine.current_engine.execute_exit(exit_tick, reason="KILL SWITCH SQUARED OFF")

    broadcast_sync({
        "type": "KILL_SWITCH",
        "active": system_state.state_machine.is_kill_switch_active
    })
    return {"status": "SUCCESS", "is_kill_switch_active": system_state.state_machine.is_kill_switch_active, "message": f"Kill-switch {status_str}"}

@app.get("/api/trades")
def get_trades_history():
    trades = db.get_closed_trades(100)
    return {"trades": trades}

@app.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keepalive receiver
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
