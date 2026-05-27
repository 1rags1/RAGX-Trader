"""
Map Binance 5m kline ticks to synthetic 10m candles for the chart buffer.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.indicators import enrich_candle_for_debug

CandleHandler = Callable[[dict[str, Any]], Awaitable[None]]


def _merge_10m(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    ta, tb = int(a["time"]), int(b["time"])
    if ta > tb:
        a, b = b, a
    b0 = int(a["time"]) // 600 * 600
    return {
        "time": b0,
        "open": float(a["open"]),
        "high": max(float(a["high"]), float(b["high"])),
        "low": min(float(a["low"]), float(b["low"])),
        "close": float(b["close"]),
        "volume": float(a["volume"]) + float(b["volume"]),
        "is_final": bool(a.get("is_final")) and bool(b.get("is_final")),
    }


def _partial_from_first_half(a: dict[str, Any], b0: int) -> dict[str, Any]:
    return {
        "time": b0,
        "open": float(a["open"]),
        "high": float(a["high"]),
        "low": float(a["low"]),
        "close": float(a["close"]),
        "volume": float(a["volume"]),
        "is_final": False,
    }


class TenMinuteKlineBridge:
    """Consumes normalized 5m candles; emits enriched 10m candles."""

    def __init__(self, emit: CandleHandler) -> None:
        self._emit = emit
        self._m5: dict[int, dict[str, Any]] = {}

    async def on_five_minute(self, candle: dict[str, Any]) -> None:
        t = int(candle["time"])
        self._m5[t] = dict(candle)
        b0 = (t // 600) * 600
        for k in list(self._m5.keys()):
            if k < b0 - 1800:
                del self._m5[k]

        a = self._m5.get(b0)
        b = self._m5.get(b0 + 300)
        ten: dict[str, Any] | None = None
        if a is not None and b is not None:
            ten = _merge_10m(a, b)
        elif a is not None:
            ten = _partial_from_first_half(a, b0)

        if ten is not None:
            await self._emit(enrich_candle_for_debug(ten))
