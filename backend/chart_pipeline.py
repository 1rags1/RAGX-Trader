"""
Deprecated alias: use `backend.candle_processor` for new code.

Re-exports the same symbols so older imports keep working.
"""

from backend.candle_processor import (
    dedupe_sort_candles,
    is_valid_candle,
    log_history_batch,
    pipeline_log,
)

__all__ = [
    "dedupe_sort_candles",
    "is_valid_candle",
    "log_history_batch",
    "pipeline_log",
]
