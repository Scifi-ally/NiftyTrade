"""SQLite persistence layer for signals, orders, trades, sizing decisions, and P&L."""
import sqlite3
import json
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from app.config import settings
from app.core.logging import logger

IST = ZoneInfo("Asia/Kolkata")

class Database:
    """Manages SQLite storage ensuring state survives restarts."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Create tables if they don't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 1. Signals
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    token TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    contract_side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target REAL NOT NULL,
                    bar_index INTEGER NOT NULL,
                    raw_json TEXT
                )
            """)

            # 2. Orders
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    token TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    trigger_price REAL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    filled_at TEXT,
                    fill_price REAL,
                    latency_ms REAL DEFAULT 0.0
                )
            """)

            # 3. Trades
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    token TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    option_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    initial_stop REAL NOT NULL,
                    current_stop REAL NOT NULL,
                    target REAL NOT NULL,
                    exit_time TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    gross_pnl REAL DEFAULT 0.0,
                    charges REAL DEFAULT 0.0,
                    net_pnl REAL DEFAULT 0.0,
                    status TEXT NOT NULL
                )
            """)

            # 4. Sizing decisions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sizing_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    token TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    available_capital REAL NOT NULL,
                    allocation_pct REAL NOT NULL,
                    budget REAL NOT NULL,
                    lot_size INTEGER NOT NULL,
                    quantity INTEGER NOT NULL,
                    reason TEXT NOT NULL
                )
            """)

            # 5. Daily P&L
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_pnl (
                    date TEXT PRIMARY KEY,
                    realised_pnl REAL DEFAULT 0.0,
                    charges REAL DEFAULT 0.0,
                    net_pnl REAL DEFAULT 0.0,
                    trade_count INTEGER DEFAULT 0
                )
            """)

            conn.commit()
            logger.info(f"SQLite database initialized at {self.db_path}")

    def save_signal(self, signal: Dict[str, Any]) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signals (timestamp, token, symbol, contract_side, entry_price, stop_loss, target, bar_index, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal["timestamp"],
                signal["token"],
                signal["symbol"],
                signal["contract_side"],
                signal["entry_price"],
                signal["stop_loss"],
                signal["target"],
                signal.get("bar_index", 0),
                json.dumps(signal)
            ))
            conn.commit()
            return cursor.lastrowid

    def save_order(self, order: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO orders (
                    order_id, mode, token, symbol, transaction_type, order_type, quantity, price, trigger_price, status, created_at, filled_at, fill_price, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(order["order_id"]),
                order["mode"],
                order["token"],
                order["symbol"],
                order["transaction_type"],
                order["order_type"],
                order["quantity"],
                order["price"],
                order.get("trigger_price", 0.0),
                order["status"],
                order["created_at"],
                order.get("filled_at"),
                order.get("fill_price"),
                order.get("latency_ms", 0.0)
            ))
            conn.commit()

    def save_trade(self, trade: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO trades (
                    trade_id, mode, token, symbol, option_type, quantity, entry_time, entry_price, initial_stop, current_stop, target, exit_time, exit_price, exit_reason, gross_pnl, charges, net_pnl, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade["trade_id"],
                trade["mode"],
                trade["token"],
                trade["symbol"],
                trade["option_type"],
                trade["quantity"],
                trade["entry_time"],
                trade["entry_price"],
                trade["initial_stop"],
                trade["current_stop"],
                trade["target"],
                trade.get("exit_time"),
                trade.get("exit_price"),
                trade.get("exit_reason"),
                trade.get("gross_pnl", 0.0),
                trade.get("charges", 0.0),
                trade.get("net_pnl", 0.0),
                trade["status"]
            ))
            conn.commit()

    def save_sizing_decision(self, decision: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sizing_decisions (
                    timestamp, token, symbol, available_capital, allocation_pct, budget, lot_size, quantity, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision["timestamp"],
                decision["token"],
                decision["symbol"],
                decision["available_capital"],
                decision["allocation_pct"],
                decision["budget"],
                decision["lot_size"],
                decision["quantity"],
                decision["reason"]
            ))
            conn.commit()

    def get_open_trade(self) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN' LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_closed_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time DESC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_today_trade_count(self) -> int:
        today_str = datetime.now(tz=IST).strftime("%Y-%m-%d")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM trades WHERE entry_time LIKE ? ", (f"{today_str}%",))
            return cursor.fetchone()[0]

    def get_today_pnl(self) -> Dict[str, float]:
        today_str = datetime.now(tz=IST).strftime("%Y-%m-%d")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT SUM(gross_pnl), SUM(charges), SUM(net_pnl)
                FROM trades
                WHERE exit_time LIKE ? AND status = 'CLOSED'
            """, (f"{today_str}%",))
            row = cursor.fetchone()
            gross = row[0] or 0.0
            charges = row[1] or 0.0
            net = row[2] or 0.0
            return {
                "gross_pnl": round(gross, 2),
                "charges": round(charges, 2),
                "net_pnl": round(net, 2)
            }

db = Database()
