"""
Market regime detection (RAGX-Trader)

Classifies recent price action using ADX(14) from pandas-ta-classic — pure functions only,
no I/O. Intended for optional weight adjustment in the signal engine (wiring is separate).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pandas_ta_classic as ta

# Minimum OHLC rows before regime is considered meaningful.
_MIN_BARS_REGIME = 20

_ADX_TRENDING_MIN = 25.0
_ADX_RANGING_MAX = 20.0


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


def _resolve_adx_series(adx_df: pd.DataFrame) -> pd.Series | None:
    """Pick the ADX column from a pandas_ta_classic ``adx`` result frame."""
    if adx_df is None or adx_df.empty:
        return None
    cols = list(adx_df.columns)
    if "ADX_14" in adx_df.columns:
        return adx_df["ADX_14"]
    for name in cols:
        if "ADX" in str(name).upper():
            return adx_df[name]
    # Last row index names (in case columns are non-standard); then first column (ADX first in ta.adx).
    last = adx_df.iloc[-1]
    if isinstance(last, pd.Series):
        for idx in last.index:
            if "ADX" in str(idx).upper():
                return adx_df[idx]
    if len(cols) > 0:
        # Last resort: ``ta.adx`` builds the frame with ADX as the first column.
        return adx_df.iloc[:, 0]
    return None


def detect_market_regime(df: pd.DataFrame) -> str:
    """
    Label the latest bar's regime using ADX(14) (with 14-period directional components per
    ``ta.adx`` defaults).

    Returns
    -------
    ``"trending"`` — latest ADX >= 25
    ``"ranging"`` — latest ADX < 20
    ``"unknown"`` — fewer than 20 rows, missing OHLC, ADX missing/NaN, non-finite ADX, or
    20 <= ADX < 25 (transition band; treat as do-not-trade for regime-specific sizing).

    Notes
    -----
    Column names must include ``high``, ``low``, and ``close`` (lowercase).
    """
    if df is None or not isinstance(df, pd.DataFrame):
        return "unknown"
    if len(df) < _MIN_BARS_REGIME:
        return "unknown"
    for col in ("high", "low", "close"):
        if col not in df.columns:
            return "unknown"

    high = df["high"]
    low = df["low"]
    close = df["close"]
    adx_out = ta.adx(high=high, low=low, close=close, length=14)
    if adx_out is None:
        return "unknown"

    series = _resolve_adx_series(adx_out)
    if series is None or series.empty:
        return "unknown"

    raw_last = series.iloc[-1]
    adx_val = _finite_or_none(raw_last)
    if adx_val is None:
        return "unknown"

    if adx_val >= _ADX_TRENDING_MIN:
        return "trending"
    if adx_val < _ADX_RANGING_MAX:
        return "ranging"
    return "unknown"


def get_regime_adjusted_weights(regime: str) -> dict[str, float]:
    """
    Strategy leg weights and net vote thresholds keyed by regime label.

    Keys match weighted-signal engine leg names plus ``net_buy_threshold`` / ``net_sell_threshold``.
    """
    r = (regime or "").strip().lower()
    if r == "trending":
        return {
            "ema_trend": 35.0,
            "macd_momentum": 25.0,
            "price_structure": 25.0,
            "rsi_reversal": 5.0,
            "bollinger_context": 5.0,
            "net_buy_threshold": 30.0,
            "net_sell_threshold": -30.0,
        }
    if r == "ranging":
        return {
            "ema_trend": 10.0,
            "macd_momentum": 10.0,
            "price_structure": 10.0,
            "rsi_reversal": 35.0,
            "bollinger_context": 35.0,
            "net_buy_threshold": 20.0,
            "net_sell_threshold": -20.0,
        }
    return {
        "ema_trend": 20.0,
        "macd_momentum": 20.0,
        "price_structure": 20.0,
        "rsi_reversal": 20.0,
        "bollinger_context": 20.0,
        "net_buy_threshold": 24.0,
        "net_sell_threshold": -24.0,
    }


if __name__ == "__main__":
    # Sanity: empty / short / missing columns
    assert detect_market_regime(pd.DataFrame()) == "unknown"
    assert detect_market_regime(pd.DataFrame({"close": [1, 2, 3]})) == "unknown"

    n = 80
    rng = pd.Series(range(n), dtype=float)
    _df = pd.DataFrame(
        {
            "high": 100 + rng * 0.5 + 1,
            "low": 100 + rng * 0.5 - 1,
            "close": 100 + rng * 0.5,
        }
    )
    reg = detect_market_regime(_df)
    assert reg in ("trending", "ranging", "unknown"), reg

    w_trend = get_regime_adjusted_weights("trending")
    w_range = get_regime_adjusted_weights("ranging")
    w_unk = get_regime_adjusted_weights("unknown")
    assert set(w_trend) == set(w_range) == set(w_unk)
    assert w_trend["ema_trend"] == 35.0
    assert w_range["rsi_reversal"] == 35.0
    assert w_unk["ema_trend"] == 20.0
    assert get_regime_adjusted_weights("bogus") == w_unk

    print("regime_detector sanity checks passed (regime sample:", reg, ")")
