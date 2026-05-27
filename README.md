# RAGX-Trader

**v0.2.0 — Regime-aware strategy engine.** Release notes: [CHANGELOG.md](CHANGELOG.md).

Local trading dashboard: **FastAPI** backend + **vanilla HTML/CSS/JS** frontend, **TradingView Lightweight Charts** candlesticks, **Binance Spot** kline WebSocket → your browser via **FastAPI WebSocket**.

Rule-based signals, regime-aware weights, and local walk-forward backtests — focused on research and display (no auto-trading).

### v0.2.0 highlights

- **ADX regime** (`trending` / `ranging` / `unknown`) with **dynamic leg weights** in the combined strategy.
- **Backtest costs:** 0.1% fees + 0.05% slippage per side; aggregates use cost-adjusted PnL.
- **Trade plans:** optional **volatility-based position sizing** (ATR) for display.
- **Timeframe comparison:** risk-adjusted ranking across intervals.

**Configuration:** No new env vars. **`RAGX_BINANCE_REGION`** (`com` / `us` / `auto`) still selects Binance Global vs Binance.US (see [Run the app](#run-the-app)).

**Tip:** `GET /api/backtest/compare` shows which timeframe scores best on recent history for the active symbol.

**Breaking changes:** None (backward compatible).

## Project layout

```
RAGX-Trader/
├── backend/
│   ├── __init__.py              # Python package marker
│   ├── main.py                  # App startup, REST, static mount, /ws/chart
│   ├── binance_stream.py        # Binance WebSocket client + kline parsing
│   ├── binance_rest.py          # Binance REST klines (startup history seed)
│   ├── candle_history.py        # Rolling OHLCV buffer (indicator input)
│   ├── strategies.py            # Rule-based RSI / MACD / BB strategies
│   ├── strategy_engine.py       # Combines votes → final dashboard signal
│   ├── signal_logger.py         # Append-only CSV signal audit log
│   ├── websocket_broadcaster.py # Fan-out to all browser connections
│   └── indicators.py            # pandas-ta-classic RSI / MACD / Bollinger
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── chart.js             # Lightweight Charts setup + bar updates
│       ├── indicators_panel.js  # Indicator readings side panel
│       ├── strategy_panel.js    # Combined strategy signal UI
│       └── app.js               # WebSocket, status UI, REST hydrate
├── requirements.txt
└── README.md
```

## Prerequisites

- **Python 3.10+** recommended (3.9+ should work).
- **VS Code** (or any editor) with a terminal.

## Install (Windows / macOS / Linux)

Open a terminal **in the project root** (`RAGX-Trader`, the folder that contains `backend/` and `requirements.txt`).

### 1. Create and activate a virtual environment (recommended)

**Windows (PowerShell):**

```powershell
cd c:\Users\ragha\Desktop\CODING\RAGX-Trader
python -m venv .venv
# If `python` is not found, use the launcher: py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
cd /path/to/RAGX-Trader
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

(Use the same command in PowerShell or bash after activating the venv.)

## Run the app

**Always run from the project root** so imports resolve (`backend.main`).

```powershell
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

### Binance Global vs Binance US (HTTP 451)

Default is **`RAGX_BINANCE_REGION=com`** (Binance Global only). If you see **HTTP 451** in the server log or the dashboard feed stays **Off**, your network blocks Global — set **`us`** or **`auto`** before starting.

With **`auto`**, the app pings **Binance Global** once; if that returns **HTTP 451**, it switches to **Binance.US**.

```powershell
$env:RAGX_BINANCE_REGION="auto"  # Global if OK, else US (set this if you are in the US)
$env:RAGX_BINANCE_REGION="us"    # Binance.US only
$env:RAGX_BINANCE_REGION="com"   # Binance Global only (default; fails with 451 if blocked)
```

**`GET /api/config`** returns **`binance_region`**: `"com"` or `"us"`.

Open a browser: **http://127.0.0.1:8000/**

Stop the server: `Ctrl+C` in the terminal.

## VS Code tips

1. **File → Open Folder** → select `RAGX-Trader`.
2. Terminal: **Terminal → New Terminal** (ensure cwd is project root).
3. Optional: install the **Python** extension; select the interpreter from `.venv`.
4. To debug, you can add a launch config that runs `uvicorn` with `"cwd": "${workspaceFolder}"`.

## API / endpoints

| Path               | Purpose                                      |
|--------------------|----------------------------------------------|
| `/`                | Dashboard (`frontend/index.html`)            |
| `/ws/chart`        | WebSocket — candles, status, indicators JSON |
| `/api/indicators`  | Latest RSI / MACD / Bollinger snapshot       |
| `/api/strategy`    | Combined rule-based signal + per-strategy (regime + weights when warm) |
| `/api/backtest`    | Walk-forward backtest on REST candles (cost-adjusted metrics)      |
| `/api/backtest/compare` | Rank timeframes (risk-adjusted scoring) for active symbol     |
| `/api/candles`     | Historical OHLC bars for chart `setData`   |
| `/api/config`      | `binance_region` (`com` = Global, `us` = Binance.US) |
| `/api/health`      | JSON health check                            |

Signal audit CSV (created automatically): `data/signals.csv` in the project root.

## Data flow (Binance → chart)

1. **Binance REST** loads up to **300** recent **1m** klines at startup (`binance_rest.py`) into **`candle_history.py`** so indicators are warm before the stream ticks.
2. **Binance** pushes **kline** events on `wss://stream.binance.com:9443/ws/btcusdt@kline_1m`.
3. **`binance_stream.py`** parses each event into the same normalized candle shape; **`main.py`** **upserts** it into the rolling buffer and recomputes **`indicators.py`** on the full **OHLCV** `DataFrame`.
4. **`main.py`** broadcasts **candle** + **indicators** JSON via **`websocket_broadcaster.py`**; **`GET /api/indicators`** mirrors the latest snapshot.
5. The **browser** connects to **`/ws/chart`**, applies **`series.update()`** in **`chart.js`** for the chart only; **`indicators_panel.js`** renders numeric readings (no chart overlays).
6. **`strategies.py`** evaluates three rule sets on the same rolling history; **`strategy_engine.py`** merges votes; each tick is appended to **`data/signals.csv`** and pushed as WebSocket **`strategy`** messages for **`strategy_panel.js`**.

## License

Use and modify for your own trading research. Not financial advice.
