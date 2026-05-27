"""
Strategy evaluation for RAGX-Trader dashboard.

Delegates signal logic to `weighted_signal_engine`: five weighted legs summed into
a net score, then BUY / SELL / NEUTRAL with plain-English copy. When indicators are
warm, ADX-based regime detection selects leg weights and vote thresholds via
``regime_detector`` before scoring.

No order placement — JSON for UI and SQLite signal history (on marker events).
"""

from __future__ import annotations

from typing import Any

from backend.explanation_layer import apply_explanations
from backend.market_summary import build_market_summary
from backend.regime_detector import detect_market_regime, get_regime_adjusted_weights
from backend.signal_markers import SignalMarkerStore
from backend.strategy_annotations import build_strategy_annotations
from backend.trade_plan import build_suggested_trade_plan
from backend.weighted_signal_engine import DEFAULT_WEIGHTS, run_weighted_strategies


def regime_advice_for(regime: str | None) -> str:
    """Short dashboard copy tied to ADX regime (or warmup / unclear)."""
    if regime == "trending":
        return (
            "Focus on trend-following setups. Mean-reversion signals are suppressed."
        )
    if regime == "ranging":
        return "Focus on range-bound setups. Trend signals are suppressed."
    return "Wait for clearer market conditions before sizing up."


def evaluate_dashboard_strategy(
    df,
    indicator_snap: dict[str, Any],
    *,
    timeframe: str = "1m",
    symbol: str | None = None,
    marker_store: SignalMarkerStore | None = None,
) -> dict[str, Any]:
    """Full payload for API/WebSocket (`annotations`, `signal_markers` for chart). Includes regime-aware weights when data is warm."""
    sufficient = bool(indicator_snap.get("sufficient_data"))

    if not sufficient:
        strategies, _ = run_weighted_strategies(df, indicator_snap)
        regime: str | None = None
        regime_weights: dict[str, float] = dict(DEFAULT_WEIGHTS)
        final: dict[str, Any] = {
            "signal": "neutral",
            "confidence": 0,
            "explanation": (
                "Indicators are still warming up. The system will wait until readings are stable "
                "before suggesting buy or sell."
            ),
            "explanation_detail": "sufficient_data is false until RSI, MACD, and Bollinger are all available.",
        }
    else:
        regime_label = detect_market_regime(df)
        regime_weights = get_regime_adjusted_weights(regime_label)
        strategies, engine_final = run_weighted_strategies(
            df, indicator_snap, weights=regime_weights
        )
        regime = regime_label
        final = dict(engine_final)
        if regime == "unknown":
            expl = (final.get("explanation") or "").rstrip()
            suffix = (
                " Market regime is unclear (ADX transition zone); "
                "consider waiting for clearer conditions."
            )
            final["explanation"] = expl + suffix

    tf = timeframe.strip() if isinstance(timeframe, str) and timeframe.strip() else "1m"
    sym_key: str | None = None
    if isinstance(symbol, str) and symbol.strip():
        sym_key = symbol.strip().upper()
    annotations = build_strategy_annotations(
        df,
        indicator_snap,
        strategies,
        timeframe=tf,
        symbol=sym_key,
    )
    if marker_store is not None:
        marker_store.ingest(
            df,
            strategies,
            final,
            timeframe=tf,
            symbol=sym_key,
            sufficient_data=sufficient,
        )
        signal_markers = marker_store.visible_for_chart(df, timeframe=tf, symbol=sym_key)
    else:
        signal_markers = []

    suggested_trade_plan = build_suggested_trade_plan(
        df,
        signal=str((final or {}).get("signal") or "neutral"),
        sufficient_data=sufficient,
    )

    base = {
        "sufficient_data": sufficient,
        "final": final,
        "strategies": strategies,
        "annotations": annotations,
        "signal_markers": signal_markers,
        "suggested_trade_plan": suggested_trade_plan,
        "regime": regime,
        "regime_weights": regime_weights,
        "regime_advice": regime_advice_for(regime),
    }
    sym_s = sym_key if sym_key else ""
    out = apply_explanations(
        base,
        indicator_snap,
        symbol=sym_s,
        timeframe=tf,
    )
    out["market_summary"] = build_market_summary(out)
    return out
