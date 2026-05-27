"""
FastAPI application entry (RAGX-Trader)

Purpose:
  - Start/stop background tasks (Binance consumer) with the app lifespan.
  - Seed rolling candle history from Binance REST so indicators warm up quickly.
  - Evaluate rule-based strategies, persist final signals to SQLite, expose REST + WebSocket.
  - Serve the static HTML/CSS/JS frontend from ../frontend.

Run from project root:
  uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config
from backend.market_config import build_api_config_payload, validate_dashboard_market_or_die
from backend.backtest_engine import run_backtest, run_backtest_compare
from backend.binance_stream import run_binance_kline_stream
from backend.candle_history import CandleHistoryBuffer
from backend.candle_processor import process_historical_batch, process_live_candle
from backend.indicators import compute_indicator_snapshot, empty_indicator_snapshot
from backend.investor_context_builder import build_investor_context
from backend.investor_score_engine import build_investor_score
from backend.investor_summary_generator import generate_rule_based_summary
from backend.investor_diagnostics import (
    build_investor_diagnostics,
    ensure_investor_data_health,
    investor_data_health_touch,
    investor_provider_error_clear,
    investor_provider_error_touch,
)
from backend.market_data_provider import create_market_data_provider
from backend.news_provider import create_news_provider
from backend.investor_universe import (
    DEFAULT_STOCK_OPPORTUNITY_UNIVERSE,
    get_universe_item,
    is_eligible_stock_opportunity,
)
from backend.performance_metrics import build_performance_summary
from backend.signal_history import SignalHistoryRepository
from backend.signal_markers import SignalMarkerStore
from backend.strategy_engine import evaluate_dashboard_strategy
from backend.symbol_manager import SymbolManager
from backend.timeframes import (
    INTERVAL_SECONDS,
    fetch_history_candles,
    is_allowed_interval,
    to_chart_bars,
)
from backend.websocket_broadcaster import WebSocketBroadcaster

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
if os.environ.get("RAGX_CANDLE_DEBUG", "").strip().lower() in ("1", "true", "yes"):
    logging.getLogger("backend.candle_history").setLevel(logging.DEBUG)
# RAGX_CANDLE_PROCESSOR_DEBUG=1 or RAGX_CANDLE_PIPELINE=1 → candle_processor logs (see candle_processor.py)

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"
SIGNAL_HISTORY_DB_PATH = ROOT_DIR / "data" / "signal_history.sqlite"
SIGNAL_MARKERS_JSONL_PATH = ROOT_DIR / "data" / "signal_markers.jsonl"

HISTORY_MAX_BARS = 500
HISTORY_SEED_LIMIT = 300


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _close_series_from_ts(ts_data: dict[str, Any] | None) -> list[float]:
    pts = (ts_data or {}).get("points") or []
    out: list[float] = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            out.append(float(raw))
        except (TypeError, ValueError):
            continue
    return out


def _period_return_pct_from_series(ts_data: dict[str, Any] | None) -> float | None:
    vals = _close_series_from_ts(ts_data)
    if len(vals) < 2:
        return None
    first, last = vals[0], vals[-1]
    if first <= 0:
        return None
    return ((last - first) / first) * 100.0


def _sparkline_points_payload(ts_data: dict[str, Any] | None, max_points: int = 48) -> list[dict[str, Any]]:
    pts = (ts_data or {}).get("points") or []
    if not pts:
        return []
    trimmed = pts[-max_points:] if len(pts) > max_points else pts
    out: list[dict[str, Any]] = []
    for p in trimmed:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            t_raw = p.get("time") if "time" in p else p.get("t")
            out.append({"time": int(t_raw), "close": float(raw)})
        except (TypeError, ValueError):
            continue
    return out


def _summarize_two_sentences(text: str | None) -> str:
    """Short thesis for compact cards."""
    if not text or not str(text).strip():
        return ""
    t = str(text).strip()
    chunks = re.split(r"(?<=[.!?])\s+", t)
    if len(chunks) <= 2:
        return t if (t.endswith(".") or t.endswith("?") or t.endswith("!")) else t + "."
    return " ".join(chunks[:2]).strip()


def _format_market_cap_usd(usd: float | None) -> str | None:
    if usd is None or usd <= 0:
        return None
    if usd >= 1e12:
        return f"${usd / 1e12:.2f}T cap"
    if usd >= 1e9:
        return f"${usd / 1e9:.2f}B cap"
    if usd >= 1e6:
        return f"${usd / 1e6:.2f}M cap"
    return f"${usd:,.0f} cap"


def _format_market_cap_chip(usd: float | None) -> str | None:
    """Compact market cap text for chips (no suffix words)."""
    if usd is None or usd <= 0:
        return None
    if usd >= 1e12:
        return f"${usd / 1e12:.2f}T"
    if usd >= 1e9:
        return f"${usd / 1e9:.2f}B"
    if usd >= 1e6:
        return f"${usd / 1e6:.2f}M"
    return f"${usd:,.0f}"


def _format_price_display(v: float | None) -> str | None:
    if v is None or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f"${f:,.2f}" if f >= 1.0 else f"${f:.4f}".rstrip("0").rstrip(".")


def _format_signed_money(v: float | None) -> str | None:
    if v is None or not isinstance(v, (int, float)):
        return None
    f = float(v)
    sym = "+" if f >= 0 else "−"
    av = abs(f)
    suf = f"{av:,.2f}" if av >= 1 else f"{av:.4f}".rstrip("0").rstrip(".")
    return f"{sym}${suf}"


def _format_signed_pct(v: float | None) -> str | None:
    if v is None or not isinstance(v, (int, float)):
        return None
    f = float(v)
    sym = "+" if f >= 0 else "−"
    return f"{sym}{abs(f):.2f}%"


def _derive_quote_fields(quote: dict[str, Any], points: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill price / day change from local series when provider omits (demo / gaps)."""
    q = dict(quote)
    prices: list[float] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            prices.append(float(raw))
        except (TypeError, ValueError):
            continue
    if q.get("price") is None and prices:
        q["price"] = prices[-1]
    if q.get("change_dollar") is None and q.get("price") is not None and q.get("previous_close") is not None:
        try:
            q["change_dollar"] = float(q["price"]) - float(q["previous_close"])
        except (TypeError, ValueError):
            pass
    if q.get("change_dollar") is None and len(prices) >= 2:
        q["change_dollar"] = prices[-1] - prices[-2]
    if q.get("change_percent") is None and len(prices) >= 2 and prices[-2] > 0:
        q["change_percent"] = (prices[-1] - prices[-2]) / prices[-2] * 100.0
    return q


def _period_range_from_points(points: list[dict[str, Any]], interval_u: str) -> tuple[str | None, str | None]:
    vals: list[float] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            vals.append(float(raw))
        except (TypeError, ValueError):
            continue
    if len(vals) < 2:
        return None, None
    lo, hi = min(vals), max(vals)
    label = f"{interval_u} range"
    return label, f"${lo:,.2f} – ${hi:,.2f}"


def _rating_badge_class(rating: str | None) -> str:
    r = (rating or "").strip().lower()
    if r == "bullish":
        return "bullish"
    if r == "cautious":
        return "cautious"
    return "neutral"


def _build_key_metrics(
    fundamentals: dict[str, Any],
    spark_points: list[dict[str, Any]],
    interval_u: str,
    breakdown: dict[str, Any],
) -> list[dict[str, str]]:
    """Non-empty metric chips only (no placeholder rows)."""
    chips: list[dict[str, str]] = []
    mc = _format_market_cap_chip(fundamentals.get("market_cap_usd") if isinstance(fundamentals, dict) else None)
    if mc:
        chips.append({"label": "Mkt cap", "value": mc})
    sector = fundamentals.get("sector") if isinstance(fundamentals, dict) else None
    if isinstance(sector, str) and sector.strip():
        chips.append({"label": "Sector", "value": sector.strip()[:48]})
    hi = fundamentals.get("week52_high") if isinstance(fundamentals, dict) else None
    lo = fundamentals.get("week52_low") if isinstance(fundamentals, dict) else None
    try:
        if hi is not None and lo is not None:
            hf, lf = float(hi), float(lo)
            chips.append({"label": "52W", "value": f"${lf:,.2f} – ${hf:,.2f}"})
    except (TypeError, ValueError):
        pass
    if not any(c["label"] == "52W" for c in chips):
        plab, pval = _period_range_from_points(spark_points, interval_u)
        if plab and pval:
            chips.append({"label": plab, "value": pval})
    vol_label = None
    if isinstance(fundamentals, dict) and fundamentals.get("annual_volatility_pct") is not None:
        try:
            vol_label = f"{float(fundamentals['annual_volatility_pct']):.1f}% ann. vol"
        except (TypeError, ValueError):
            vol_label = None
    if not vol_label and isinstance(breakdown, dict):
        vrl = breakdown.get("volatility_risk_level")
        if isinstance(vrl, str) and vrl.strip():
            vol_label = vrl.strip()
    if vol_label:
        chips.append({"label": "Risk", "value": vol_label})
    return chips


def _weekly_performance_from_series(ts_data: dict[str, Any] | None) -> float:
    points = (ts_data or {}).get("points") or []
    prices: list[float] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            prices.append(float(raw))
        except (TypeError, ValueError):
            continue
    if len(prices) < 6:
        return 0.0
    last = prices[-1]
    ref = prices[-6]
    if ref <= 0:
        return 0.0
    return (last - ref) / ref


def last_candle_from_dataframe(df: pd.DataFrame | None) -> dict | None:
    """Build a candle-shaped dict from the last OHLCV row (for logging / cold start)."""
    if df is None or df.empty:
        return None
    r = df.iloc[-1]
    return {
        "time": int(r["time"]),
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
        "volume": float(r["volume"]),
        "is_final": True,
    }


async def _seed_candle_history(buffer: CandleHistoryBuffer, symbol_manager: SymbolManager) -> None:
    """Blocking HTTP in a thread so the event loop stays responsive."""
    try:
        sym, _ = await symbol_manager.rest_and_ws_prefix()
        candles = await asyncio.to_thread(fetch_history_candles, sym, "1m", HISTORY_SEED_LIMIT)
        cleaned, st = process_historical_batch(
            candles,
            label=f"seed_1m({sym})",
            interval_sec=INTERVAL_SECONDS["1m"],
        )
        if st["rejected_malformed"] or st["duplicate_overwrites"]:
            logger.info("Seed candle_processor stats: %s", st)
        await buffer.replace_with(cleaned)
        logger.info("Seeded candle history (%s): %s bars", sym, len(cleaned))
    except Exception:
        logger.warning(
            "Startup history fetch failed; indicators will warm up from live stream only.",
            exc_info=True,
        )


def _price_from_candle(candle: dict | None) -> float | None:
    if not candle or candle.get("close") is None:
        return None
    try:
        return float(candle["close"])
    except (TypeError, ValueError):
        return None


async def _persist_final_signal_history(
    lock: asyncio.Lock,
    repo: SignalHistoryRepository,
    marker_store: SignalMarkerStore,
    ts: str,
    candle: dict | None,
    snap: dict,
    strategy_payload: dict,
) -> None:
    """One SQLite row per new chart marker (BUY/SELL/EXIT), not every candle tick."""
    batch = marker_store.last_ingested_batch
    if not batch:
        return
    price = _price_from_candle(candle)
    async with lock:
        await asyncio.to_thread(
            repo.persist_marker_batch,
            batch,
            decision_utc=ts,
            indicator_snap=snap,
            strategy_payload=strategy_payload,
            price_at_signal=price,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.configure_binance()
    validate_dashboard_market_or_die()
    broadcaster: WebSocketBroadcaster = WebSocketBroadcaster()
    buffer = CandleHistoryBuffer(max_bars=HISTORY_MAX_BARS)
    signal_history_lock = asyncio.Lock()

    app.state.broadcaster = broadcaster
    app.state.candle_buffer = buffer
    app.state.symbol_manager = SymbolManager()
    symbol_manager: SymbolManager = app.state.symbol_manager
    app.state.signal_history_lock = signal_history_lock
    app.state.market_data_provider = create_market_data_provider()
    app.state.news_provider = create_news_provider()
    ensure_investor_data_health(app.state)
    app.state.signal_history_repo = SignalHistoryRepository(SIGNAL_HISTORY_DB_PATH)
    app.state.signal_marker_store = SignalMarkerStore(log_path=SIGNAL_MARKERS_JSONL_PATH)
    app.state.binance_connected = False
    app.state.last_update_utc: str | None = None
    app.state.current_interval = "1m"
    app.state.binance_restart_lock = asyncio.Lock()
    app.state.latest_indicators = empty_indicator_snapshot()
    sym_boot, _ = await symbol_manager.rest_and_ws_prefix()
    app.state.latest_strategy = evaluate_dashboard_strategy(
        await buffer.snapshot_dataframe(),
        app.state.latest_indicators,
        timeframe=app.state.current_interval,
        symbol=sym_boot,
        marker_store=app.state.signal_marker_store,
    )

    await _seed_candle_history(buffer, symbol_manager)
    df0 = await buffer.snapshot_dataframe()
    snap0 = compute_indicator_snapshot(df0)
    strat0 = evaluate_dashboard_strategy(
        df0,
        snap0,
        timeframe=app.state.current_interval,
        symbol=sym_boot,
        marker_store=app.state.signal_marker_store,
    )
    app.state.latest_indicators = snap0
    app.state.latest_strategy = strat0

    async def on_binance_status(connected: bool, ts: str | None) -> None:
        app.state.binance_connected = connected
        await broadcaster.broadcast_json(
            {
                "type": "status",
                "binance_connected": connected,
                "last_update_utc": ts or app.state.last_update_utc,
            }
        )

    async def on_candle(candle: dict) -> None:
        tail = await buffer.last_open_time()
        iv_sec = INTERVAL_SECONDS[app.state.current_interval]
        proc = process_live_candle(
            candle,
            tail_time=tail,
            interval_sec=iv_sec,
            logical_interval=app.state.current_interval,
        )
        if proc is None:
            logger.warning(
                "[candle_processor] dropped live candle time=%s",
                candle.get("time"),
            )
            return
        df = await buffer.upsert_and_snapshot_dataframe(proc)
        snap = compute_indicator_snapshot(df)
        sym_live, _ = await app.state.symbol_manager.rest_and_ws_prefix()
        strat = evaluate_dashboard_strategy(
            df,
            snap,
            timeframe=app.state.current_interval,
            symbol=sym_live,
            marker_store=app.state.signal_marker_store,
        )
        app.state.latest_indicators = snap
        app.state.latest_strategy = strat

        ts = _utc_iso_now()
        app.state.last_update_utc = ts

        await _persist_final_signal_history(
            signal_history_lock,
            app.state.signal_history_repo,
            app.state.signal_marker_store,
            ts,
            proc,
            snap,
            strat,
        )

        await broadcaster.broadcast_json(
            {
                "type": "candle",
                "last_update_utc": ts,
                "data": proc,
            }
        )
        await broadcaster.broadcast_json(
            {
                "type": "indicators",
                "last_update_utc": ts,
                "data": snap,
            }
        )
        await broadcaster.broadcast_json(
            {
                "type": "strategy",
                "last_update_utc": ts,
                "data": strat,
            }
        )

    app.state.on_binance_candle = on_candle
    app.state.on_binance_status = on_binance_status

    _, ws_prefix = await symbol_manager.rest_and_ws_prefix()
    binance_task = asyncio.create_task(
        run_binance_kline_stream("1m", ws_prefix, on_candle, on_binance_status),
        name="binance-kline-stream",
    )
    app.state.binance_task = binance_task

    yield

    shutdown_task = app.state.binance_task
    if shutdown_task and not shutdown_task.done():
        shutdown_task.cancel()
        try:
            await shutdown_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="RAGX-Trader", lifespan=lifespan)


async def snapshot_buffer_chart_bars(app: FastAPI, limit: int) -> list[dict]:
    """Last `limit` bars from the buffer through the same batch pipeline as REST history."""
    buffer: CandleHistoryBuffer = app.state.candle_buffer
    df = await buffer.snapshot_dataframe()
    if df is None or df.empty:
        return []
    tail = df.tail(int(limit))
    raw_rows: list[dict[str, Any]] = []
    for _, r in tail.iterrows():
        vol = (
            float(r["volume"])
            if "volume" in r.index and pd.notna(r["volume"])
            else 0.0
        )
        raw_rows.append(
            {
                "time": int(r["time"]),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": vol,
                "is_final": True,
            }
        )
    iv = getattr(app.state, "current_interval", "1m")
    cleaned, st = process_historical_batch(
        raw_rows,
        label=f"snapshot_buffer({iv})",
        interval_sec=INTERVAL_SECONDS[iv],
    )
    if st["rejected_malformed"] or st["duplicate_overwrites"] or st["sequence_warnings"]:
        logger.info("snapshot_buffer_chart_bars candle_processor stats: %s", st)
    return to_chart_bars(cleaned)


async def switch_timeframe(app: FastAPI, interval: str) -> list[dict]:
    """
    Cancel the Binance stream task, replace history from REST (200 bars),
    rebroadcast indicators/strategy, start a new stream for `interval`.
    """
    interval = interval.strip()
    if not is_allowed_interval(interval):
        raise ValueError("unsupported interval")

    broadcaster: WebSocketBroadcaster = app.state.broadcaster
    buffer: CandleHistoryBuffer = app.state.candle_buffer
    signal_history_lock: asyncio.Lock = app.state.signal_history_lock

    async with app.state.binance_restart_lock:
        t = app.state.binance_task
        if (
            t
            and not t.done()
            and interval == app.state.current_interval
        ):
            return await snapshot_buffer_chart_bars(app, 200)

        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        sym, ws_prefix = await app.state.symbol_manager.rest_and_ws_prefix()
        candles = await asyncio.to_thread(fetch_history_candles, sym, interval, 200)
        cleaned, st = process_historical_batch(
            candles,
            label=f"timeframe_switch({sym},{interval})",
            interval_sec=INTERVAL_SECONDS[interval],
        )
        if st["rejected_malformed"] or st["duplicate_overwrites"]:
            logger.info("Timeframe switch candle_processor stats: %s", st)
        await buffer.replace_with(cleaned)
        df = await buffer.snapshot_dataframe()
        snap = compute_indicator_snapshot(df)
        strat = evaluate_dashboard_strategy(
            df,
            snap,
            timeframe=interval,
            symbol=sym,
            marker_store=app.state.signal_marker_store,
        )
        app.state.latest_indicators = snap
        app.state.latest_strategy = strat
        app.state.current_interval = interval

        ts = _utc_iso_now()
        app.state.last_update_utc = ts
        await _persist_final_signal_history(
            signal_history_lock,
            app.state.signal_history_repo,
            app.state.signal_marker_store,
            ts,
            last_candle_from_dataframe(df),
            snap,
            strat,
        )

        chart_bars = to_chart_bars(cleaned)
        await broadcaster.broadcast_json(
            {
                "type": "timeframe_changed",
                "interval": interval,
                "bars": chart_bars,
                "last_update_utc": ts,
            }
        )
        await broadcaster.broadcast_json(
            {
                "type": "indicators",
                "last_update_utc": ts,
                "data": snap,
            }
        )
        await broadcaster.broadcast_json(
            {
                "type": "strategy",
                "last_update_utc": ts,
                "data": strat,
            }
        )

        on_c = app.state.on_binance_candle
        on_s = app.state.on_binance_status
        app.state.binance_task = asyncio.create_task(
            run_binance_kline_stream(interval, ws_prefix, on_c, on_s),
            name="binance-kline-stream",
        )

        return chart_bars


class TimeframeBody(BaseModel):
    interval: str = Field(..., min_length=2, max_length=8)


@app.get("/api/health")
async def health():
    """Simple liveness probe (optional for ops / browser devtools)."""
    return {"status": "ok", "service": "RAGX-Trader"}


@app.get("/api/config")
async def api_config():
    """Active market identity — same symbol, region, and Spot endpoints as chart + engine."""
    sm: SymbolManager = app.state.symbol_manager
    sym = await sm.spot_symbol()
    interval = getattr(app.state, "current_interval", "1m")
    return build_api_config_payload(
        binance_region=config.effective_region,
        symbol=sym,
        interval=interval,
    )


@app.post("/api/timeframe")
async def api_post_timeframe(body: TimeframeBody):
    """Switch Binance stream interval, refresh buffer from REST (200 bars), rebroadcast."""
    try:
        bars = await switch_timeframe(app, body.interval)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": True,
        "interval": app.state.current_interval,
        "bars": bars,
    }


@app.get("/api/candles/history")
async def api_candles_history(
    interval: str = Query(..., description="1m, 5m, 10m, 15m, 30m, 1d"),
    limit: int = Query(200, ge=1, le=1000),
):
    """
    Historical OHLC from Binance REST (last `limit` closed bars), formatted for Lightweight Charts.
    """
    iv = interval.strip()
    if not is_allowed_interval(iv):
        raise HTTPException(status_code=400, detail="unsupported interval")
    sm: SymbolManager = app.state.symbol_manager
    sym = await sm.spot_symbol()
    try:
        candles = await asyncio.to_thread(fetch_history_candles, sym, iv, limit)
    except Exception:
        logger.exception("Binance history fetch failed")
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch klines from Binance",
        ) from None
    cleaned, st = process_historical_batch(
        candles,
        label=f"api_history({sym},{iv})",
        interval_sec=INTERVAL_SECONDS[iv],
    )
    if st["rejected_malformed"] or st["duplicate_overwrites"]:
        logger.info("api/candles/history candle_processor stats: %s", st)
    return {"symbol": sym, "interval": iv, "bars": to_chart_bars(cleaned)}


@app.get("/api/candles")
async def api_candles(limit: int = Query(300, ge=1, le=500)):
    """
    OHLC bars from the in-memory buffer for chart seeding (TradingView setData).
    Without this, the browser only receives live update() ticks and the scale can
    collapse to a few cents when a single flat 1m bar is shown.
    """
    return {"bars": await snapshot_buffer_chart_bars(app, limit)}


@app.get("/api/indicators")
async def api_indicators():
    """
    Latest indicator snapshot computed from the in-memory rolling history.

    Values are informational only — not buy/sell advice. When `sufficient_data`
    is false, some or all fields may be null while the buffer warms up.
    """
    return app.state.latest_indicators


@app.get("/api/strategy")
async def api_strategy():
    """
    Latest combined rule-based signal plus per-strategy breakdown.

    Includes regime fields when indicators are warm: ``regime``, ``regime_weights``,
    ``regime_advice`` (plus ``final``, ``strategies``, annotations, trade plan, etc.).
    For display and research only — no orders are sent.
    """
    return app.state.latest_strategy


@app.get("/api/signal-markers")
async def api_signal_markers(limit: int = Query(400, ge=1, le=800)):
    """
    Recent persisted signal markers (append-only log + in-memory store) for review.
    Does not place orders.
    """
    store: SignalMarkerStore = app.state.signal_marker_store
    return {"markers": store.all_markers(limit=limit)}


@app.get("/api/signal-history")
async def api_signal_history(
    limit: int = Query(150, ge=1, le=500),
    action: list[str] | None = Query(
        None,
        description="Filter by signal_type: repeat param (e.g. action=buy&action=sell)",
    ),
    timeframe: str | None = Query(None, description="Filter by chart interval (e.g. 5m)"),
):
    """
    Recent rows from the durable SQLite log (final BUY/SELL/EXIT events with context).
    For analysis only — does not place orders.
    """
    repo: SignalHistoryRepository = app.state.signal_history_repo
    rows = await asyncio.to_thread(
        repo.fetch_recent,
        limit,
        actions=action,
        timeframe=timeframe,
    )
    return {"limit": limit, "action": action, "timeframe": timeframe, "rows": rows}


@app.get("/api/performance/summary")
async def api_performance_summary(
    signals_limit: int = Query(100, ge=10, le=500),
    backtest_last_n: int = Query(50, ge=1, le=200),
    backtest_interval: str | None = Query(
        None,
        description="Interval for stored backtest trades; defaults to current chart interval",
    ),
):
    """
    Compact aggregates for the dashboard: current regime, recent signal mix, optional
    backtest win rate from the last persisted run (see GET /api/backtest).
    """
    strat = app.state.latest_strategy or {}
    regime = strat.get("regime")
    repo: SignalHistoryRepository = app.state.signal_history_repo
    aggregates = await asyncio.to_thread(repo.fetch_recent_signal_aggregates, signals_limit)
    sm: SymbolManager = app.state.symbol_manager
    sym = await sm.spot_symbol()
    iv_raw = backtest_interval if isinstance(backtest_interval, str) else None
    iv = iv_raw.strip() if iv_raw and iv_raw.strip() else getattr(app.state, "current_interval", "1m")
    win_stats = await asyncio.to_thread(
        repo.fetch_backtest_win_rate,
        **{"symbol": sym, "interval": iv, "last_n": backtest_last_n},
    )
    return build_performance_summary(
        current_regime=regime,
        signal_aggregates=aggregates,
        backtest_win=win_stats,
    )


@app.get("/api/backtest")
async def api_backtest(
    interval: str = Query(..., description="1m, 5m, 10m, 15m, 30m, 1d"),
    limit: int = Query(600, ge=80, le=1000),
    include_trades: bool = Query(False, description="Include closed trade list (trimmed to 200)"),
):
    """
    Walk-forward backtest on historical REST candles using the live strategy + trade-plan rules.
    Uses the **active dashboard symbol** only (same Spot feed as the chart).
    Local computation only — does not place orders or touch the live marker log.
    """
    sm: SymbolManager = app.state.symbol_manager
    sym = await sm.spot_symbol()
    try:
        out = await asyncio.to_thread(
            run_backtest,
            symbol=sym,
            interval=interval.strip(),
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    repo: SignalHistoryRepository = app.state.signal_history_repo
    iv = interval.strip()
    if out.get("ok") and isinstance(out.get("closed_trades"), list) and out["closed_trades"]:
        await asyncio.to_thread(
            repo.replace_backtest_trades,
            **{
                "run_utc": _utc_iso_now(),
                "symbol": sym,
                "interval": iv,
                "trades": out["closed_trades"],
            },
        )
    if not include_trades:
        out["closed_trades"] = []
    elif isinstance(out.get("closed_trades"), list) and len(out["closed_trades"]) > 200:
        out["closed_trades"] = out["closed_trades"][-200:]
        out["closed_trades_truncated"] = True
    return out


@app.get("/api/backtest/compare")
async def api_backtest_compare(
    limit: int = Query(600, ge=80, le=1000),
):
    """
    Run a local backtest for each allowed timeframe; returns per-interval metrics and the best by total return.
    Uses the **active dashboard symbol** only (same Spot feed as the chart).
    """
    sm: SymbolManager = app.state.symbol_manager
    sym = await sm.spot_symbol()
    out = await asyncio.to_thread(
        run_backtest_compare,
        symbol=sym,
        limit=limit,
    )
    return out


_INVESTOR_RANGE = frozenset({"1D", "5D", "1M", "6M", "1Y"})


def _investor_interval_alias(raw: str | None, default: str = "6M") -> str:
    u = (raw or default).upper().strip()
    return u if u in _INVESTOR_RANGE else default


def _investor_timeseries_payload(provider: Any, data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {"provider": getattr(provider, "name", "unknown"), **data}
    dbg = os.environ.get("RAGX_DEBUG", "").strip().lower() in ("1", "true", "yes")
    if dbg:
        payload["debug"] = {
            "detail": data.get("debug_detail") or data.get("message"),
            "error": bool(data.get("error")),
            "resolution": data.get("resolution"),
            "data_source": data.get("data_source"),
        }
    return payload


def _investor_record_chart(app: FastAPI, data: dict[str, Any]) -> None:
    if data.get("error"):
        investor_provider_error_touch(
            app.state,
            chart={
                "provider": str(data.get("provider") or getattr(app.state.market_data_provider, "name", "unknown")),
                "message": str(data.get("message") or "Timeseries load failed"),
                "detail": (data.get("debug_detail") or data.get("upstream_note") or ""),
            },
        )
    else:
        investor_provider_error_clear(app.state, chart=True)
        investor_data_health_touch(app.state, chart=True, price=True)


def _investor_record_quote(app: FastAPI, data: dict[str, Any]) -> None:
    if data.get("error"):
        investor_provider_error_touch(
            app.state,
            price={
                "provider": str(data.get("provider") or getattr(app.state.market_data_provider, "name", "unknown")),
                "message": str(data.get("message") or "Quote load failed"),
                "detail": (data.get("debug_detail") or ""),
            },
        )
    else:
        investor_provider_error_clear(app.state, price=True)
        investor_data_health_touch(app.state, price=True)


def _investor_record_profile(app: FastAPI, data: dict[str, Any]) -> None:
    if data.get("error"):
        investor_provider_error_touch(
            app.state,
            profile={
                "provider": str(data.get("provider") or getattr(app.state.market_data_provider, "name", "unknown")),
                "message": str(data.get("message") or "Company profile failed"),
                "detail": (data.get("debug_detail") or ""),
            },
        )
    else:
        investor_provider_error_clear(app.state, profile=True)


def _investor_record_news(provider_name: str, news_result: dict[str, Any], app: FastAPI) -> None:
    if news_result.get("error"):
        investor_provider_error_touch(
            app.state,
            news={
                "provider": provider_name,
                "message": str(news_result.get("message") or "News unavailable"),
                "detail": (
                    " ".join(
                        str(x)
                        for x in (news_result.get("reason"), news_result.get("debug_detail"))
                        if isinstance(x, str) and x.strip()
                    )
                ).strip()
                or str(news_result.get("reason") or ""),
            },
        )
    else:
        investor_provider_error_clear(app.state, news=True)


@app.get("/api/investor/search")
async def api_investor_search(
    q: str = Query(..., min_length=1, max_length=80, description="Ticker or company name"),
):
    """
    Investor stock search (mock universe for now).
    Route contract stays stable so a real provider can replace mock data later.
    """
    provider = app.state.market_data_provider
    items = provider.search_symbols(q)
    return {
        "q": q,
        "count": len(items),
        "items": items,
        "provider": getattr(provider, "name", "unknown"),
    }


@app.get("/api/investor/time-series")
async def api_investor_time_series(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
    interval: str = Query("1M", min_length=2, max_length=4, description="1D, 5D, 1M, 6M, 1Y"),
):
    """Investor chart data (backward-compatible alias — prefer /api/investor/timeseries)."""
    iv = _investor_interval_alias(interval, "1M")
    provider = app.state.market_data_provider
    data = provider.get_time_series(symbol, iv)
    payload = _investor_timeseries_payload(provider, data)
    _investor_record_chart(app, data)
    return payload


@app.get("/api/investor/timeseries")
async def api_investor_timeseries(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
    range_: str = Query("6M", alias="range", min_length=2, max_length=4, description="1D, 5D, 1M, 6M, 1Y"),
):
    """Historical closes for Investor Mode charts."""
    iv = _investor_interval_alias(range_, "6M")
    provider = app.state.market_data_provider
    data = provider.get_time_series(symbol, iv)
    payload = _investor_timeseries_payload(provider, data)
    _investor_record_chart(app, data)
    return payload


@app.get("/api/investor/quote")
async def api_investor_quote(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
):
    """Live-ish last quote for Investor Mode."""
    provider = app.state.market_data_provider
    data = provider.get_quote(symbol)
    payload = {**data, "provider": getattr(provider, "name", "unknown")}
    _investor_record_quote(app, data)
    return payload


@app.get("/api/investor/profile")
async def api_investor_profile(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
):
    """Company symbol metadata (live Finnhub profile2 when configured)."""
    provider = app.state.market_data_provider
    data = provider.get_company_profile(symbol)
    payload = {**data, "provider": getattr(provider, "name", "unknown")}
    _investor_record_profile(app, data)
    return payload


@app.get("/api/investor/news")
async def api_investor_news(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
    limit: int = Query(72, ge=1, le=120, description="Max articles returned (sorted by date, newest first)"),
):
    """Recent company news for investor panel (real provider feeds only — never synthesized)."""
    provider = app.state.news_provider
    news_result = provider.get_company_news_result(symbol)
    items = news_result.get("items", [])

    def _sort_key(x: dict) -> str:
        v = x.get("published_at")
        return v if isinstance(v, str) else ""

    sorted_items = sorted(
        [x for x in items if isinstance(x, dict)],
        key=_sort_key,
        reverse=True,
    )
    capped = sorted_items[: int(limit)]
    out = {
        "symbol": symbol.upper(),
        "provider": getattr(provider, "name", "unknown"),
        "count": len(capped),
        "items": capped,
        "error": bool(news_result.get("error")),
        "message": news_result.get("message"),
        "reason": news_result.get("reason"),
    }
    if news_result.get("debug_detail"):
        out["debug_detail"] = news_result.get("debug_detail")
    pname = getattr(provider, "name", "unknown")
    _investor_record_news(pname, news_result, app)
    if not news_result.get("error"):
        investor_data_health_touch(app.state, news=True)
    return out


@app.get("/api/investor/diagnostics")
async def api_investor_diagnostics():
    """Demo vs live mode, provider wiring, last successful investor fetches."""
    market_provider = app.state.market_data_provider
    news_provider = app.state.news_provider
    hp = getattr(app.state, "investor_data_health", None)
    diag = build_investor_diagnostics(market_provider, news_provider, hp if isinstance(hp, dict) else {})
    return diag


@app.get("/api/investor/score")
async def api_investor_score(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
    interval: str = Query("6M", min_length=2, max_length=4, description="1D, 5D, 1M, 6M, 1Y"),
):
    """
    Evidence-based investor score (0..100) with cautious language.
    Not financial advice.
    """
    market_provider = app.state.market_data_provider
    news_provider = app.state.news_provider
    sym = symbol.upper()
    benchmark_ts = market_provider.get_time_series("SPY", interval)
    benchmark_weekly = _weekly_performance_from_series(benchmark_ts)
    quote = market_provider.get_quote(sym)
    ts_data = market_provider.get_time_series(sym, interval)
    news_result = news_provider.get_company_news_result(sym)
    news = news_result.get("items", [])
    profile = market_provider.get_company_profile(sym)
    scored = build_investor_score(
        symbol=sym,
        quote=quote,
        time_series=ts_data,
        news_items=news,
        profile=profile,
        market_context={"benchmark_symbol": "SPY", "benchmark_weekly_performance": benchmark_weekly},
        news_feed_error=bool(news_result.get("error")),
    )
    return {
        "provider": getattr(market_provider, "name", "unknown"),
        "news_provider": getattr(news_provider, "name", "unknown"),
        "interval": interval.upper(),
        **scored,
    }


@app.get("/api/investor/research-summary")
async def api_investor_research_summary(
    symbol: str = Query(..., min_length=1, max_length=16, description="Ticker symbol"),
    interval: str = Query("6M", min_length=2, max_length=4, description="1D, 5D, 1M, 6M, 1Y"),
):
    """Rule-based AI-style research summary with cited sources."""
    market_provider = app.state.market_data_provider
    news_provider = app.state.news_provider
    sym = symbol.upper()
    benchmark_ts = market_provider.get_time_series("SPY", interval)
    benchmark_weekly = _weekly_performance_from_series(benchmark_ts)
    quote = market_provider.get_quote(sym)
    ts_data = market_provider.get_time_series(sym, interval)
    news_result = news_provider.get_company_news_result(sym)
    news = news_result.get("items", [])
    profile = market_provider.get_company_profile(sym)
    scored = build_investor_score(
        symbol=sym,
        quote=quote,
        time_series=ts_data,
        news_items=news,
        profile=profile,
        market_context={"benchmark_symbol": "SPY", "benchmark_weekly_performance": benchmark_weekly},
        news_feed_error=bool(news_result.get("error")),
    )
    context = build_investor_context(
        selected_ticker=sym,
        price_trend=ts_data,
        news_list=news,
        score_breakdown=scored,
        risk_factors=scored.get("risk_factors", []),
        user_question=None,
    )
    summary = generate_rule_based_summary(context, quote=quote)
    return {
        "provider": getattr(market_provider, "name", "unknown"),
        "news_provider": getattr(news_provider, "name", "unknown"),
        "interval": interval.upper(),
        "score": scored,
        "context": context,
        **summary,
    }


@app.get("/api/investor/opportunities")
async def api_investor_opportunities(
    interval: str = Query("6M", min_length=2, max_length=4, description="1D, 5D, 1M, 6M, 1Y"),
):
    """
    Rank default stock-opportunity universe (individual names only; no ETFs/index funds).
    """
    market_provider = app.state.market_data_provider
    news_provider = app.state.news_provider
    benchmark_ts = market_provider.get_time_series("SPY", interval)
    benchmark_weekly = _weekly_performance_from_series(benchmark_ts)
    ranked: list[dict[str, Any]] = []
    saw_good_price = False
    saw_good_chart = False
    saw_good_news = False
    for sym in DEFAULT_STOCK_OPPORTUNITY_UNIVERSE:
        profile = market_provider.get_company_profile(sym)
        if not is_eligible_stock_opportunity(sym, profile):
            continue
        quote = market_provider.get_quote(sym)
        ts_data = market_provider.get_time_series(sym, interval)
        news_result = news_provider.get_company_news_result(sym)
        if isinstance(quote, dict) and not quote.get("error") and quote.get("price") is not None:
            saw_good_price = True
        if isinstance(ts_data, dict) and not ts_data.get("error") and (ts_data.get("points") or []):
            saw_good_chart = True
        if isinstance(news_result, dict) and not news_result.get("error"):
            saw_good_news = True
        news = news_result.get("items", [])
        scored = build_investor_score(
            symbol=sym,
            quote=quote,
            time_series=ts_data,
            news_items=news,
            profile=profile,
            market_context={"benchmark_symbol": "SPY", "benchmark_weekly_performance": benchmark_weekly},
            news_feed_error=bool(news_result.get("error")),
        )
        sorted_news: list[dict[str, Any]] = []
        first_headline = None
        if news and isinstance(news, list):
            sorted_news = sorted(
                [x for x in news if isinstance(x, dict)],
                key=lambda x: x.get("published_at") if isinstance(x.get("published_at"), str) else "",
                reverse=True,
            )
            if sorted_news:
                first_headline = sorted_news[0].get("headline")
        meta = get_universe_item(sym) or {}
        # Prefer provider profile (real API data). Universe meta is only a fallback.
        company_name = profile.get("company_name") if isinstance(profile, dict) else None
        exchange = profile.get("exchange") if isinstance(profile, dict) else None
        asset_type = profile.get("asset_type") if isinstance(profile, dict) else None
        points_full = (ts_data or {}).get("points") or []
        ranked.append(
            {
                "ticker": sym,
                "company_name": company_name or meta.get("company_name"),
                "exchange": exchange or meta.get("exchange"),
                "asset_type": asset_type or meta.get("asset_type"),
                "current_price": quote.get("price"),
                "daily_change_percent": quote.get("change_percent"),
                "daily_change_dollar": quote.get("change_dollar"),
                "period_return_percent": _period_return_pct_from_series(ts_data),
                "sparkline_points": _sparkline_points_payload(ts_data, 48),
                "score": scored.get("score"),
                "rating": scored.get("rating"),
                "tier_hint": scored.get("explanation"),
                "why_ranked": scored.get("why_ranked"),
                "latest_headline": first_headline,
                "news_items": sorted_news[:2],
                "news_error": bool(news_result.get("error")),
                "news_message": news_result.get("message"),
                "score_breakdown": scored.get("breakdown", {}),
                "_quote": quote,
                "_points_full": points_full,
            }
        )
    ranked.sort(key=lambda x: int(x.get("score") or 0), reverse=True)
    interval_u = interval.upper().strip()
    finalized: list[dict[str, Any]] = []
    for row in ranked[:3]:
        sym = row["ticker"]
        qraw = row.pop("_quote", {})
        pts = row.pop("_points_full", [])
        dq = _derive_quote_fields(qraw, pts)
        fund = market_provider.get_opportunity_fundamentals(sym)
        bd = row.get("score_breakdown") or {}
        why_ranked = row.get("why_ranked")
        row["current_price"] = dq.get("price")
        row["daily_change_percent"] = dq.get("change_percent")
        row["daily_change_dollar"] = dq.get("change_dollar")
        row["current_price_display"] = _format_price_display(dq.get("price"))
        row["daily_change_dollars_display"] = _format_signed_money(dq.get("change_dollar"))
        row["daily_change_percent_display"] = _format_signed_pct(dq.get("change_percent"))
        row["rating_badge"] = row.get("rating")
        row["rating_badge_variant"] = _rating_badge_class(row.get("rating"))
        row["reason_short"] = _summarize_two_sentences(why_ranked)
        row["key_metrics"] = _build_key_metrics(fund, row.get("sparkline_points") or [], interval_u, bd)
        finalized.append(row)
    if saw_good_price:
        investor_data_health_touch(app.state, price=True)
    if saw_good_chart:
        investor_data_health_touch(app.state, chart=True)
    if saw_good_news:
        investor_data_health_touch(app.state, news=True)
    return {
        "provider": getattr(market_provider, "name", "unknown"),
        "news_provider": getattr(news_provider, "name", "unknown"),
        "interval": interval.upper(),
        "count": len(finalized),
        "items": finalized,
    }


@app.websocket("/ws/chart")
async def chart_websocket(websocket: WebSocket):
    """
    Browser connects here. Server pushes:
      - { "type": "status", ... }
      - { "type": "timeframe_changed", "interval", "bars", ... }
      - { "type": "candle", "last_update_utc", "data": { ... } }
      - { "type": "indicators", ... }
      - { "type": "strategy", "data": { final, strategies, annotations, signal_markers, ... }, ... }

    Client may send JSON: { "type": "set_timeframe", "interval": "5m" } (same as POST /api/timeframe).
    Plain "ping" is ignored for keepalive.
    """
    broadcaster: WebSocketBroadcaster = app.state.broadcaster
    await broadcaster.register(websocket)
    try:
        await broadcaster.broadcast_json(
            {
                "type": "status",
                "binance_connected": app.state.binance_connected,
                "last_update_utc": app.state.last_update_utc,
            }
        )
        await websocket.send_json(
            {
                "type": "indicators",
                "last_update_utc": app.state.last_update_utc,
                "data": app.state.latest_indicators,
            }
        )
        await websocket.send_json(
            {
                "type": "strategy",
                "last_update_utc": app.state.last_update_utc,
                "data": app.state.latest_strategy,
            }
        )
        while True:
            raw = await websocket.receive_text()
            if not raw or raw.strip().lower() == "ping":
                continue
            try:
                cmd = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(cmd, dict) or cmd.get("type") != "set_timeframe":
                continue
            iv = cmd.get("interval")
            if not isinstance(iv, str):
                continue
            try:
                await switch_timeframe(app, iv)
            except ValueError:
                try:
                    await websocket.send_json(
                        {"type": "error", "message": "unsupported interval"}
                    )
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.unregister(websocket)


# Static site last so /api and /ws routes win.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    logger.warning("Frontend directory missing: %s", FRONTEND_DIR)
