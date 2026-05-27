"""
Active Binance Spot symbol (single feed) — RAGX-Trader

Default remains BTCUSDT. Override with env RAGX_SPOT_SYMBOL=ETHUSDT.
Future multi-symbol: call set_symbol() under the same lock used for reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Final

from backend.market_config import DEFAULT_SPOT_SYMBOL

logger = logging.getLogger(__name__)

_ENV_KEY: Final = "RAGX_SPOT_SYMBOL"
_DEFAULT_SYMBOL: Final = DEFAULT_SPOT_SYMBOL
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{4,24}$")


def _normalize_symbol(raw: str) -> str:
    s = raw.strip().upper()
    if not s:
        return _DEFAULT_SYMBOL
    if not _SYMBOL_RE.fullmatch(s):
        raise ValueError(f"invalid spot symbol format: {raw!r}")
    return s


def default_spot_symbol_from_env() -> str:
    """Initial symbol from environment (used if SymbolManager gets no explicit symbol)."""
    try:
        return _normalize_symbol(os.getenv(_ENV_KEY, _DEFAULT_SYMBOL))
    except ValueError:
        logger.warning("Invalid %s — falling back to %s", _ENV_KEY, _DEFAULT_SYMBOL)
        return _DEFAULT_SYMBOL


class SymbolManager:
    """
    Holds the one active Spot symbol for REST + combined-stream kline WebSocket paths.

    WebSocket paths use lowercase (e.g. ethusdt@kline_1m); REST uses uppercase.
    """

    def __init__(self, initial: str | None = None) -> None:
        sym = _normalize_symbol(initial) if initial else default_spot_symbol_from_env()
        self._symbol = sym
        self._lock = asyncio.Lock()
        logger.info("Active Spot symbol: %s", self._symbol)

    async def spot_symbol(self) -> str:
        """Thread-safe read (use before asyncio.to_thread I/O)."""
        async with self._lock:
            return self._symbol

    def spot_symbol_sync(self) -> str:
        """Read without awaiting; safe only while no concurrent set_symbol (startup)."""
        return self._symbol

    async def ws_kline_stream_prefix(self) -> str:
        """Lowercase symbol for Binance combined stream path segment."""
        async with self._lock:
            return self._symbol.lower()

    def ws_kline_stream_prefix_sync(self) -> str:
        return self._symbol.lower()

    async def rest_and_ws_prefix(self) -> tuple[str, str]:
        """Consistent (REST uppercase, WebSocket lowercase) pair under one lock."""
        async with self._lock:
            return self._symbol, self._symbol.lower()

    async def set_symbol(self, symbol: str) -> str:
        """
        Switch active symbol (reserved for a future API). Not used by routes yet.

        Callers must restart streams and replace buffers separately.
        """
        new_sym = _normalize_symbol(symbol)
        async with self._lock:
            old = self._symbol
            self._symbol = new_sym
        if old != new_sym:
            logger.info("Spot symbol switched: %s -> %s", old, new_sym)
        return new_sym
