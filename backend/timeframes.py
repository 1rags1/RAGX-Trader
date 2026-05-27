"""
Timeframe definitions and history fetch for RAGX-Trader.

Binance Spot does not expose a native 10m kline; we synthesize 10m from 5m data
(REST aggregation + live 5m stream via TenMinuteKlineBridge in binance_stream).
"""

from __future__ import annotations

from typing import Any

from backend.binance_rest import fetch_spot_klines
from backend.indicators import enrich_candle_for_debug

# UI / logical intervals (must match frontend).
ALLOWED_INTERVALS: tuple[str, ...] = ("1m", "5m", "10m", "15m", "30m", "1d")

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1d": 86_400,
}


def is_open_time_on_interval_grid(unix_open_sec: int, logical_interval: str) -> bool:
    """
    True if `unix_open_sec` lies on Binance's UTC kline grid for this logical interval.

    Binance REST/WS kline `openTime` is the bar's open in milliseconds; we store seconds.
    For each supported interval, open times are multiples of the bar duration in UTC
    (e.g. 1m → % 60 == 0, 1d → % 86400 == 0). Synthetic 10m bars use the same 600s grid.
    """
    sec = INTERVAL_SECONDS.get(logical_interval)
    if not sec or sec <= 0:
        return False
    return int(unix_open_sec) % sec == 0


def is_allowed_interval(interval: str) -> bool:
    return interval in INTERVAL_SECONDS


def binance_rest_interval(logical: str) -> str:
    """REST/WS interval string accepted by Binance."""
    if logical == "10m":
        return "5m"
    return logical


def kline_stream_path(ws_symbol_lower: str, logical: str) -> str:
    """
    Binance combined stream path segment, e.g. btcusdt@kline_1m.

    `ws_symbol_lower` must match REST symbol lowercased (BTCUSDT -> btcusdt).
    """
    seg = ws_symbol_lower.strip().lower()
    return f"{seg}@kline_{binance_rest_interval(logical)}"


def _merge_two_5m_to_10m(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
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


def aggregate_5m_bars_to_10m(fives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sorted 5m closed bars -> 10m bars (one per pair in the same 10-minute UTC bucket)."""
    if not fives:
        return []
    by_bucket: dict[int, list[dict[str, Any]]] = {}
    for c in sorted(fives, key=lambda x: int(x["time"])):
        t = int(c["time"])
        b0 = (t // 600) * 600
        by_bucket.setdefault(b0, []).append(c)

    out: list[dict[str, Any]] = []
    for b0 in sorted(by_bucket.keys()):
        parts = sorted(by_bucket[b0], key=lambda x: int(x["time"]))
        if len(parts) >= 2:
            out.append(enrich_candle_for_debug(_merge_two_5m_to_10m(parts[0], parts[1])))
        elif len(parts) == 1:
            p = parts[0]
            out.append(
                enrich_candle_for_debug(
                    {
                        "time": b0,
                        "open": float(p["open"]),
                        "high": float(p["high"]),
                        "low": float(p["low"]),
                        "close": float(p["close"]),
                        "volume": float(p["volume"]),
                        "is_final": bool(p.get("is_final", True)),
                    }
                )
            )
    return out


def fetch_history_candles(
    spot_symbol: str,
    logical_interval: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """
    Last `limit` candles for Lightweight Charts (unix `time` in seconds, OHLC floats).

    Uses Binance REST. For 10m, pulls extra 5m bars and aggregates.
    """
    if not is_allowed_interval(logical_interval):
        raise ValueError(f"unsupported interval: {logical_interval!r}")
    sym = spot_symbol.strip().upper()
    lim = max(1, min(int(limit), 1000))

    if logical_interval == "10m":
        need_5m = min(1000, max(lim * 2 + 4, lim + 10))
        fives = fetch_spot_klines(sym, "5m", need_5m)
        tens = aggregate_5m_bars_to_10m(fives)
        return tens[-lim:]

    return fetch_spot_klines(sym, logical_interval, lim)


def to_chart_bars(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip to fields Lightweight Charts expects (via candle_processor)."""
    from backend.candle_processor import to_chart_payload

    return to_chart_payload(candles)
