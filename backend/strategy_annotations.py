"""
Strategy payload `annotations` (chart primitives).

The beginner dashboard keeps the main chart minimal (candles + trend line + buy/sell markers).
This object stays in the API for compatibility; `items` is always empty. Geometry and labels
that used to draw on-chart are not emitted — use the sidebar and `signal_markers` instead.
"""

from __future__ import annotations

from typing import Any

ANNOTATION_VERSION = 1


def empty_annotations(*, timeframe: str = "1m", symbol: str | None = None) -> dict[str, Any]:
    return {
        "version": ANNOTATION_VERSION,
        "timeframe": timeframe,
        "symbol": symbol,
        "items": [],
    }


def build_strategy_annotations(
    _df: Any,
    _indicator_snap: dict[str, Any],
    _strategies: list[dict[str, Any]],
    *,
    timeframe: str,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Return empty `items`; strategy logic and indicators are unchanged elsewhere."""
    tf = timeframe.strip() if isinstance(timeframe, str) and timeframe.strip() else "1m"
    sym_k: str | None = None
    if isinstance(symbol, str) and symbol.strip():
        sym_k = symbol.strip().upper()
    return empty_annotations(timeframe=tf, symbol=sym_k)
