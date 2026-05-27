"""
Indicator calculations (RAGX-Trader)

Purpose:
  Pure functions that take OHLCV history (pandas) and return the latest RSI, MACD,
  and Bollinger Band values using pandas-ta-classic. No I/O, no trading logic.

Warm-up:
  Indicators need a minimum number of rows; callers should check `sufficient_data`
  before treating numbers as meaningful.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd
import pandas_ta_classic as ta

# Minimum rows so MACD/RSI/BB have a fair chance to be non-NaN on the last bar.
MIN_BARS_MACD = 40
MIN_BARS_RSI = 15
MIN_BARS_BB = 21
MIN_BARS_EMA_TREND = 20
MINIMUM_BARS_REQUIRED = max(MIN_BARS_MACD, MIN_BARS_RSI, MIN_BARS_BB)
CHART_OVERLAY_VERSION = 1


def typical_price(candle: Mapping[str, Any]) -> float:
    """Classic typical price: (high + low + close) / 3."""
    return (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3.0


def enrich_candle_for_debug(candle: Mapping[str, Any]) -> dict[str, Any]:
    """Shallow copy plus `typical_price` for optional debug / enrichment."""
    out = dict(candle)
    out["typical_price"] = round(typical_price(candle), 2)
    return out


def _finite_or_none(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def empty_chart_overlays() -> dict[str, Any]:
    """
    Single EMA trend line on the chart. Bollinger scalars live in `bollinger`.
    """
    return {
        "version": CHART_OVERLAY_VERSION,
        "lines": {
            "ema_20": [],
        },
        "meta": {
            "trend_line": {"type": "ema", "period": MIN_BARS_EMA_TREND},
        },
    }


def _chart_line_points(df: pd.DataFrame, value_series: pd.Series | None) -> list[dict[str, float | int]]:
    """Drop NaN/inf; `time` is unix seconds aligned to OHLC rows."""
    if df is None or df.empty or value_series is None:
        return []
    if "time" not in df.columns:
        return []
    s = value_series.reindex(df.index)
    n = len(df)
    times = pd.to_numeric(df["time"], errors="coerce")
    out: list[dict[str, float | int]] = []
    for i in range(n):
        t_raw = times.iloc[i]
        if pd.isna(t_raw):
            continue
        fv = _finite_or_none(s.iloc[i])
        if fv is None:
            continue
        out.append({"time": int(t_raw), "value": float(round(fv, 6))})
    return out


def _compute_chart_overlays(df: pd.DataFrame) -> dict[str, Any]:
    """EMA(20) on close — smooth, responsive trend cue; no extra series."""
    base = empty_chart_overlays()
    if df is None or df.empty:
        return base
    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)) or "time" not in df.columns:
        return base

    close = pd.to_numeric(df["close"], errors="coerce")
    if close.isna().all():
        return base

    bars = int(len(df))
    if bars >= MIN_BARS_EMA_TREND:
        ema = ta.ema(close, length=MIN_BARS_EMA_TREND)
        if ema is not None:
            base["lines"]["ema_20"] = _chart_line_points(df, ema)

    return base


def empty_indicator_snapshot() -> dict[str, Any]:
    """Default API/WebSocket payload when nothing is computed yet."""
    return {
        "sufficient_data": False,
        "bars_used": 0,
        "minimum_bars_required": MINIMUM_BARS_REQUIRED,
        "as_of_candle_time": None,
        "last_close": None,
        "rsi_14": None,
        "macd": {"line": None, "signal": None, "histogram": None},
        "bollinger": {"upper": None, "middle": None, "lower": None},
        "chart_overlays": empty_chart_overlays(),
    }


def compute_indicator_snapshot(df: pd.DataFrame) -> dict[str, Any]:
    """
    Compute the latest RSI(14), MACD(12,26,9), and Bollinger Bands(20, 2.0).

    `df` must contain columns: open, high, low, close, volume (volume unused here).
    Returns JSON-friendly floats (rounded) or None where undefined.
    """
    snap = empty_indicator_snapshot()
    if df is None or df.empty:
        return snap

    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return snap

    bars = int(len(df))
    snap["bars_used"] = bars
    snap["as_of_candle_time"] = int(df["time"].iloc[-1]) if "time" in df.columns else None
    snap["chart_overlays"] = _compute_chart_overlays(df)
    close_series = pd.to_numeric(df["close"], errors="coerce") if "close" in df.columns else None
    lc = _finite_or_none(close_series.iloc[-1]) if close_series is not None and len(close_series) else None
    snap["last_close"] = round(float(lc), 2) if lc is not None else None

    if bars < MINIMUM_BARS_REQUIRED:
        return snap

    close = pd.to_numeric(df["close"], errors="coerce")
    if close.isna().all():
        return snap

    # --- RSI(14) ---
    rsi_series = ta.rsi(close, length=14)
    rsi_last = _finite_or_none(rsi_series.iloc[-1]) if rsi_series is not None and len(rsi_series) else None

    # --- MACD(12,26,9) ---
    macd_tbl = ta.macd(close, fast=12, slow=26, signal=9)
    macd_line = macd_signal = macd_hist = None
    if macd_tbl is not None and not macd_tbl.empty:
        last_macd = macd_tbl.iloc[-1]
        macd_line = _finite_or_none(last_macd.get("MACD_12_26_9"))
        macd_signal = _finite_or_none(last_macd.get("MACDs_12_26_9"))
        macd_hist = _finite_or_none(last_macd.get("MACDh_12_26_9"))
        if macd_line is None and macd_signal is None and macd_hist is None:
            cols = [c for c in macd_tbl.columns if isinstance(c, str)]
            if len(cols) >= 3:
                macd_line = _finite_or_none(last_macd[cols[0]])
                macd_signal = _finite_or_none(last_macd[cols[1]])
                macd_hist = _finite_or_none(last_macd[cols[2]])

    # --- Bollinger Bands (20, 2) ---
    bb = ta.bbands(close, length=20, std=2.0)
    b_upper = b_mid = b_lower = None
    if bb is not None and not bb.empty:
        last_bb = bb.iloc[-1]
        for suf in ("2.0", "2"):
            lo = _finite_or_none(last_bb.get(f"BBL_20_{suf}"))
            mid = _finite_or_none(last_bb.get(f"BBM_20_{suf}"))
            hi = _finite_or_none(last_bb.get(f"BBU_20_{suf}"))
            if lo is not None or mid is not None or hi is not None:
                b_lower, b_mid, b_upper = lo, mid, hi
                break

    snap["rsi_14"] = round(rsi_last, 2) if rsi_last is not None else None
    snap["macd"] = {
        "line": round(macd_line, 6) if macd_line is not None else None,
        "signal": round(macd_signal, 6) if macd_signal is not None else None,
        "histogram": round(macd_hist, 6) if macd_hist is not None else None,
    }
    snap["bollinger"] = {
        "upper": round(b_upper, 2) if b_upper is not None else None,
        "middle": round(b_mid, 2) if b_mid is not None else None,
        "lower": round(b_lower, 2) if b_lower is not None else None,
    }

    snap["sufficient_data"] = all(
        [
            snap["rsi_14"] is not None,
            snap["macd"]["line"] is not None,
            snap["macd"]["signal"] is not None,
            snap["macd"]["histogram"] is not None,
            snap["bollinger"]["upper"] is not None,
            snap["bollinger"]["middle"] is not None,
            snap["bollinger"]["lower"] is not None,
        ]
    )
    return snap
