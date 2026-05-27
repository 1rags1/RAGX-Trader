"""
Shared Binance kline → internal candle shape (OHLCV + open time in unix seconds).

`time` is the kline **open** instant: Binance `openTime` in ms, converted with // 1000.
Open times follow Binance's UTC grid for the stream interval (same grid as REST klines).

WebSocket object `k` fields (Spot):
  t  open time (ms)   T  close time (ms)
  o  open  h  high  l  low  c  close  v  base volume
  x  is this kline closed?
"""

from __future__ import annotations

from typing import Any

from backend.indicators import enrich_candle_for_debug


def candle_from_binance_ws_k(k: dict[str, Any]) -> dict[str, Any]:
    """Normalize Binance WebSocket kline payload `k` to one candle dict."""
    open_ms = int(k["t"])
    candle: dict[str, Any] = {
        "time": open_ms // 1000,
        "open": float(k["o"]),
        "high": float(k["h"]),
        "low": float(k["l"]),
        "close": float(k["c"]),
        "volume": float(k["v"]),
        "is_final": bool(k["x"]),
    }
    return enrich_candle_for_debug(candle)


def candle_from_binance_rest_row(row: list[Any]) -> dict[str, Any]:
    """Normalize REST kline array row to one closed candle dict."""
    open_ms = int(row[0])
    candle: dict[str, Any] = {
        "time": open_ms // 1000,
        "open": float(row[1]),
        "high": float(row[2]),
        "low": float(row[3]),
        "close": float(row[4]),
        "volume": float(row[5]),
        "is_final": True,
    }
    return enrich_candle_for_debug(candle)
