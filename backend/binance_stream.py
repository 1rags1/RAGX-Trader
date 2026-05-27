"""
Binance Spot WebSocket kline stream (RAGX-Trader)

Maintains one WebSocket for a symbol's klines at a configurable interval. Reconnects
with backoff. Logical interval "10m" uses the 5m stream plus TenMinuteKlineBridge.

`ws_symbol_lower` is the stream segment (e.g. btcusdt); reuse this coroutine per symbol
by passing a different prefix when starting a task.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import WebSocketException

from backend import config
from backend.kline_normalize import candle_from_binance_ws_k
from backend.ten_minute_bridge import TenMinuteKlineBridge
from backend.timeframes import kline_stream_path

logger = logging.getLogger(__name__)

RECONNECT_DELAY_SEC = 3.0
PING_INTERVAL_SEC = 20.0
PING_TIMEOUT_SEC = 20.0

CandleHandler = Callable[[dict[str, Any]], Awaitable[None]]
StatusHandler = Callable[[bool, str | None], Awaitable[None]]


def _ws_url_for_path(stream_path: str) -> str:
    return config.ws_kline_url(stream_path)


def _normalize_kline(k: dict[str, Any]) -> dict[str, Any]:
    return candle_from_binance_ws_k(k)


async def run_binance_kline_stream(
    logical_interval: str,
    ws_symbol_lower: str,
    on_candle: CandleHandler,
    on_binance_status: StatusHandler,
) -> None:
    """
    Long-running task: one Binance WebSocket until cancelled or error+reconnect.

    logical_interval: 1m, 5m, 10m (5m feed), 15m, 30m, 1d
    ws_symbol_lower: e.g. btcusdt (must match active REST symbol, lowercased).
    """
    stream_path = kline_stream_path(ws_symbol_lower, logical_interval)
    if logical_interval == "10m":
        bridge = TenMinuteKlineBridge(on_candle)

        async def dispatch(c: dict[str, Any]) -> None:
            await bridge.on_five_minute(c)
    else:

        async def dispatch(c: dict[str, Any]) -> None:
            await on_candle(c)

    while True:
        url = _ws_url_for_path(stream_path)
        try:
            await on_binance_status(True, _utc_iso_now())
            async with websockets.connect(
                url,
                ping_interval=PING_INTERVAL_SEC,
                ping_timeout=PING_TIMEOUT_SEC,
                close_timeout=5,
            ) as ws:
                logger.info(
                    "Binance WebSocket connected: %s (symbol=%s logical=%s)",
                    url,
                    ws_symbol_lower,
                    logical_interval,
                )
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("e") != "kline":
                        continue
                    k = msg.get("k")
                    if not isinstance(k, dict):
                        continue
                    candle = _normalize_kline(k)
                    await dispatch(candle)
        except (WebSocketException, OSError, asyncio.CancelledError) as e:
            if isinstance(e, asyncio.CancelledError):
                await on_binance_status(False, _utc_iso_now())
                raise
            logger.warning("Binance stream error: %s", e)
            await on_binance_status(False, _utc_iso_now())
            await asyncio.sleep(RECONNECT_DELAY_SEC)
        except Exception:
            logger.exception("Unexpected error in Binance stream loop")
            await on_binance_status(False, _utc_iso_now())
            await asyncio.sleep(RECONNECT_DELAY_SEC)


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
