# NiftyTrades - Automated NIFTY Options Trading System

A complete, free, local automated trading system that runs **ONE** strategy—an exact port of the Pine Script indicator **"NIFTY Options | Clean Signals v4"**—exclusively on **NSE NIFTY Index Options** (NFO `OPTIDX` with underlying `NIFTY`), using **Angel One SmartAPI** for all market data and order routing.

---

## Hard Architectural Rules
1. **Zero Mock Data**: Real credentials, real instrument master, real ticks, real historical candles, real funds, and real orders. If feed or login fails, trading halts immediately. No fake or hardcoded prices.
2. **NIFTY Options Exclusively**: Any symbol other than NIFTY index (`99926000`) or NIFTY options (`OPTIDX` on `NFO`) is strictly rejected across configuration, APIs, and UI.
3. **Pine Script Strategy Fidelity**: Buy/Sell decisions are made strictly by the ported Pine Script indicator logic. Position sizing, risk management, and learning components never create or alter an entry decision.
4. **Paper Mode Default**: Always starts in `PAPER` mode on application startup. Switching to `LIVE` mode requires typing `"LIVE"` in a confirmation dialog, allowed only when no positions are open and the feed is healthy.
5. **Secrets in `.env` Only**: API keys, PINs, and TOTP secrets are stored strictly in `.env` and kept in memory only. Secrets are never logged.
6. **Free Stack Only**: Python 3.12, FastAPI + WebSocket, SQLite, TradingView Lightweight Charts (plain JS, completely local). No paid external services.

---

## Quick Start & One-Command Run

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Credentials
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your Angel One credentials:
```env
ANGEL_API_KEY=your_smartapi_api_key_here
ANGEL_CLIENT_CODE=your_client_code_here
ANGEL_PIN=your_mpin_here
ANGEL_TOTP_SECRET=your_32_char_base32_totp_secret_here
```

### 3. Run Phase 1 Diagnostic Test
```bash
python check_angel.py
```
Validates credentials, session creation, scrip master download (147k+ instruments), ATM option resolution, historical 50 candles, and WebSocket tick stream with latency stats.

### 4. Run Test Suite
```bash
pytest
```
Runs 18 unit and integration tests covering indicator math, position sizing, risk caps, trailing stop, and API endpoints.

### 5. Launch Trading System & Web UI
```bash
python main.py
```
Open **[http://127.0.0.1:8000](http://127.0.0.1:8000)** in your browser.

---

## Strategy Logic (Exact Pine Script Port)

Long-only on option premium candles (buying ATM CE or ATM PE when its own premium gives a buy setup):
- **Triggers**:
  - `reclaim`: Crossover of close above EMA(21)
  - `breakRecentHigh`: Crossover of close above highest high of past 3 bars
  - `pullbackResume`: Low of previous bar was below EMA(9), close breaks above previous high and above EMA(9)
- **Filters**:
  - `trendOK`: Close > EMA(21) and EMA(9) > EMA(9)[1]
  - `momentumOK`: RSI(14) in `[50, 78]` (or `[55, 70]` in strict mode)
  - `candleOK`: Bullish candle with body/range $\ge 0.20$ (strict $0.35$), close near high $\ge 0.55$ (strict $0.65$), range $\le 2.5 \times ATR$ (strict $1.8 \times ATR$)
  - `chaseOK`: Price distance from EMA(9) $\le 2.0 \times ATR$ (strict $1.3 \times ATR$)
  - `riskOK`: Stop distance $\le 2.5 \times ATR$ and $\le 25\%$ of premium
  - `indexOK`: (Optional) Confirms NIFTY index EMA(9) vs EMA(21) direction from previous completed bar
  - `vwapOK`: (Optional) Premium above session VWAP
  - `volumeOK`: (Optional) Relative volume $\ge minRVOL$
- **Stop & Target**:
  - `candidateStop`: $\text{round\_to\_tick}(\min(\text{lowest low of 3 bars} - 0.1 \times ATR, \text{close} - 0.8 \times ATR))$
  - `target`: $\text{round\_to\_tick}(\text{entry} + (\text{entry} - \text{stop}) \times rewardR)$
- **Exits**:
  - Stop and Target touches evaluated on **EVERY TICK**
  - Bar close exits: Confirmed trend break (2 consecutive bars below EMA21), Max hold (18 bars), Session exit (15:15 IST), Session gap (new day)
  - Cooldown: 3 bars after exit before re-entry

---

## Position Sizing & Risk Management

- **Base Allocation**: 15% of available funds per trade.
  $\text{lots} = \lfloor \text{budget} / (\text{premium} \times \text{lot\_size}) \rfloor$. If budget cannot afford 1 lot, skips trade and logs reason.
- **Adaptive Allocation (5% to 20%)**:
  - Penalties: -3% on 2 consecutive losses; -6% on 3+ consecutive losses; -3% on daily drawdown $> 50\%$ of max loss; -2% on wide stop $> 15\%$.
  - Boost: +3% after 30+ closed trades show positive expectancy and $> 55\%$ win rate.
- **Hard Risk Caps**:
  - Max loss per trade: 2% of capital (lots trimmed automatically if stop risk exceeds 2%)
  - Max daily loss: ₹5,000 (halts trading for the day)
  - Max trades per day: 5 trades
  - Max lots cap: 10 lots
  - Stale feed block: Stops new trades if no ticks for 15 seconds
  - Spread filter: Blocks entry if bid-ask spread $> 2.5\%$
  - Emergency Kill-switch: Squares off position and halts bot

---

## Indian Regulatory Charges & Tax Modeling (Paper Mode)

Paper trades model real statutory costs so net P&L reflects actual take-home:
1. **Brokerage**: ₹20 flat per executed order (₹40 round-trip)
2. **STT (Securities Transaction Tax)**: 0.1% on sell side option premium turnover
3. **Exchange Txn Charges**: 0.05% on total premium turnover
4. **SEBI Turnover Fee**: ₹10 per crore (0.0001%) on total turnover
5. **Stamp Duty**: 0.003% on buy side turnover
6. **GST**: 18% on (Brokerage + Exchange Txn + SEBI fee)

---

## User Interface

- **TradingView Lightweight Charts**: Full dark-themed candlestick chart of active option premium updating tick-by-tick over WebSocket without polling.
- **Visual Markers**:
  - Lime `BUY CE` / Fuchsia `BUY PE` signal markers with entry, stop, and target levels.
  - Distinct Yellow markers for actual bot fills.
  - Dynamic horizontal price lines: Entry (Yellow), Stop (Red, trails dynamically), Target (Teal).
- **Header HUD**: Large Net P&L (Realised + Unrealised), feed health pulsing dot, latency (ms), PAPER/LIVE toggle, Kill Switch button.
- **Trade Log Drawer**: Collapsible drawer with all trade fills, gross P&L, statutory charges, and net P&L.

---

## Project Structure

```
NiftyTrades/
├── .env.example             # Template credentials configuration
├── check_angel.py           # Phase 1 diagnostic and verification script
├── main.py                  # Server and trading engine launcher
├── pytest.ini               # Pytest configuration
├── requirements.txt         # Dependencies
├── README.md                # Documentation
├── data/
│   └── OpenAPIScripMaster.json  # Cached instrument master (147k+ instruments)
├── app/
│   ├── config.py            # Pydantic Settings & strict NIFTY validation
│   ├── core/
│   │   ├── exceptions.py    # Custom exception classes
│   │   └── logging.py       # Structured IST logging with secret masking
│   ├── adapter/
│   │   ├── base.py          # BrokerAdapter abstract base class
│   │   └── angel_one.py     # Angel One SmartAPI production adapter
│   ├── data/
│   │   ├── scrip_master.py  # Master parser, ATM resolution, lot size reader
│   │   ├── tick_stream.py   # SmartWebSocketV2 depth streaming & latency
│   │   └── candle_builder.py# Tick-to-candle engine aligned with exchange hours
│   ├── strategy/
│   │   ├── indicator.py     # Exact mathematical port of Pine Script indicator
│   │   └── state_machine.py # Multi-contract state machine & tick exits
│   ├── execution/
│   │   ├── sizing.py        # Position sizing & risk caps
│   │   ├── paper_engine.py  # Paper trading engine with Indian taxation
│   │   └── live_engine.py   # Live order router with freeze splitting
│   ├── storage/
│   │   └── db.py            # SQLite database manager
│   └── web/
│       ├── server.py        # FastAPI server & WebSocket hub
│       └── static/          # Dark theme UI (HTML, CSS, JS, Lightweight Charts)
└── tests/
    ├── test_api.py          # FastAPI endpoints & safety switch tests
    ├── test_indicator.py    # Indicator math tests
    ├── test_sizing.py       # Position sizing & risk tests
    └── test_trailing.py     # Trailing stop logic tests
```
