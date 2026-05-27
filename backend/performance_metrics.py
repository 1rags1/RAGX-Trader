"""
Dashboard-facing performance aggregates — thin helpers over SQLite + latest strategy state.
"""

from __future__ import annotations

from typing import Any

# Signal types shown on the performance dashboard (marker log uses buy/sell/exit; neutral reserved).
SIGNAL_TYPES_DASH = ("buy", "sell", "neutral", "exit")


def normalize_signal_breakdown(aggregates: dict[str, Any]) -> dict[str, Any]:
    """Map ``fetch_recent_signal_aggregates`` output to fixed keys with zero-fill."""
    raw = aggregates.get("by_type") or {}
    counts: dict[str, int] = {t: 0 for t in SIGNAL_TYPES_DASH}
    avg_conf: dict[str, float | None] = {t: None for t in SIGNAL_TYPES_DASH}
    for key, meta in raw.items():
        k = str(key).strip().lower()
        if k not in counts:
            continue
        counts[k] = int(meta.get("count") or 0)
        ac = meta.get("avg_confidence")
        if ac is None:
            avg_conf[k] = None
        else:
            try:
                avg_conf[k] = round(float(ac), 4)
            except (TypeError, ValueError):
                avg_conf[k] = None
    return {
        "signal_counts_by_type": counts,
        "avg_confidence_by_type": avg_conf,
        "signals_window": aggregates.get("limit"),
    }


def build_performance_summary(
    *,
    current_regime: str | None,
    signal_aggregates: dict[str, Any],
    backtest_win: dict[str, Any] | None,
) -> dict[str, Any]:
    nb = normalize_signal_breakdown(signal_aggregates)
    return {
        "current_regime": current_regime,
        **nb,
        "backtest_win_rate": backtest_win,
    }
