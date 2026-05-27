"""
Rolling candle history buffer (RAGX-Trader)

Purpose:
  Hold a time-ordered window of OHLCV bars for the active spot symbol. The WebSocket stream
  only pushes updates; this module merges live ticks into the same open-time bar
  and keeps enough history for RSI/MACD/Bollinger without involving WebSocket code.

Notes:
  - All public async methods serialize access with an asyncio.Lock (single event loop).
  - `upsert` replaces the last bar when `time` matches (in-progress candle updates).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from backend.candle_processor import pipeline_log

logger = logging.getLogger(__name__)


class CandleHistoryBuffer:
    """In-memory rolling store of normalized candle dicts (sorted by `time` ascending)."""

    def __init__(self, max_bars: int = 500) -> None:
        self._max = max(50, int(max_bars))
        self._bars: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def __len__(self) -> int:
        return len(self._bars)

    async def replace_with(self, candles: list[dict[str, Any]]) -> None:
        """
        Replace buffer contents with a sorted, de-duplicated list (by candle `time`).
        Used after REST seeding so indicators warm up before the live stream arrives.
        """
        async with self._lock:
            self._replace_with_unlocked(candles)

    def _replace_with_unlocked(self, candles: list[dict[str, Any]]) -> None:
        by_t: dict[int, dict[str, Any]] = {}
        for c in candles:
            t = int(c["time"])
            by_t[t] = c
        merged = sorted(by_t.values(), key=lambda x: int(x["time"]))
        self._bars = merged[-self._max :]

    async def upsert(self, candle: dict[str, Any]) -> None:
        """Append a new bar or refresh the current bar when `time` matches the tail."""
        async with self._lock:
            self._upsert_unlocked(candle)

    async def upsert_and_snapshot_dataframe(self, candle: dict[str, Any]) -> pd.DataFrame:
        """Single lock: apply live tick and return OHLCV DataFrame for indicators."""
        async with self._lock:
            self._upsert_unlocked(candle)
            return self.to_ohlcv_dataframe()

    def _upsert_unlocked(self, candle: dict[str, Any]) -> None:
        t = int(candle["time"])
        is_final = bool(candle.get("is_final"))
        close = candle.get("close")

        if self._bars and int(self._bars[-1]["time"]) == t:
            self._bars[-1] = dict(candle)
            logger.debug(
                "[RAGX candle] update (same open_time) open_time=%s is_final=%s close=%s",
                t,
                is_final,
                close,
            )
            pipeline_log(
                "[RAGX pipeline] buffer UPDATE same open_time=%s is_final=%s close=%s",
                t,
                is_final,
                close,
            )
            return
        if self._bars and t < int(self._bars[-1]["time"]):
            # Rare out-of-order tick: merge by time without breaking sort.
            self._insert_sorted_or_replace(t, dict(candle))
            self._bars = self._bars[-self._max :]
            logger.debug(
                "[RAGX candle] resync (out-of-order) open_time=%s is_final=%s close=%s",
                t,
                is_final,
                close,
            )
            pipeline_log(
                "[RAGX pipeline] buffer RESYNC out-of-order open_time=%s is_final=%s",
                t,
                is_final,
            )
            return
        self._bars.append(dict(candle))
        if len(self._bars) > self._max:
            self._bars = self._bars[-self._max :]
        logger.debug(
            "[RAGX candle] new_candle open_time=%s is_final=%s close=%s bars=%s",
            t,
            is_final,
            close,
            len(self._bars),
        )
        pipeline_log(
            "[RAGX pipeline] buffer APPEND new open_time=%s is_final=%s close=%s buf_len=%s",
            t,
            is_final,
            close,
            len(self._bars),
        )

    def _insert_sorted_or_replace(self, t: int, candle: dict[str, Any]) -> None:
        # Linear scan is fine for <= a few hundred bars.
        for i, b in enumerate(self._bars):
            if int(b["time"]) == t:
                self._bars[i] = candle
                return
        self._bars.append(candle)
        self._bars.sort(key=lambda x: int(x["time"]))

    def to_ohlcv_dataframe(self) -> pd.DataFrame:
        """
        Build a DataFrame for pandas-ta-classic. Caller must hold `lock` if concurrent
        with writers, or use `snapshot_dataframe()`.
        """
        if not self._bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "time"])
        rows = self._bars
        return pd.DataFrame(
            {
                "time": [int(b["time"]) for b in rows],
                "open": [float(b["open"]) for b in rows],
                "high": [float(b["high"]) for b in rows],
                "low": [float(b["low"]) for b in rows],
                "close": [float(b["close"]) for b in rows],
                "volume": [float(b["volume"]) for b in rows],
            }
        )

    async def snapshot_dataframe(self) -> pd.DataFrame:
        """Thread-safe copy of OHLCV history as a DataFrame (under lock)."""
        async with self._lock:
            return self.to_ohlcv_dataframe()

    async def last_open_time(self) -> int | None:
        """Unix open time of the last bar, or None if empty (for live stale checks)."""
        async with self._lock:
            if not self._bars:
                return None
            return int(self._bars[-1]["time"])
