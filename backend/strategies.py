"""
Backward-compatible shim: `run_all_strategies` returns only the sidebar rows (list).

Full evaluation (rows + final) uses `weighted_signal_engine.run_weighted_strategies`.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backend.weighted_signal_engine import run_weighted_strategies


def run_all_strategies(df: pd.DataFrame, indicator_snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Return strategy rows for callers that only need the per-leg breakdown."""
    panel, _final = run_weighted_strategies(df, indicator_snap)
    return panel


__all__ = ["run_all_strategies"]
