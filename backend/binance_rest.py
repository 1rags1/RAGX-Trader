"""
Binance Spot REST helpers (RAGX-Trader)

Purpose:
  Fetch historical klines over HTTPS so the indicator engine has enough bars on
  startup. Host follows backend.config (Binance Global or Binance.US).
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from backend import config
from backend.kline_normalize import candle_from_binance_rest_row

logger = logging.getLogger(__name__)


def fetch_spot_klines(symbol: str, interval: str, limit: int) -> list[dict[str, Any]]:
    """
    Synchronous fetch of recent closed klines (public endpoint, no API key).

    Returns candles in the same normalized shape as the WebSocket path (plus enrich).
    """
    lim = max(1, min(int(limit), 1000))
    url = config.klines_url(symbol, interval, lim)
    req = urllib.request.Request(url, headers={"User-Agent": "RAGX-Trader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Binance REST klines failed: %s", e)
        raise

    out: list[dict[str, Any]] = []
    for row in raw:
        # [ openTime, open, high, low, close, volume, closeTime, ... ]
        out.append(candle_from_binance_rest_row(row))
    return out


def fetch_spot_1m_klines(symbol: str, limit: int = 300) -> list[dict[str, Any]]:
    """Convenience: 1m spot klines for any symbol (uppercase, e.g. BTCUSDT)."""
    return fetch_spot_klines(symbol.strip().upper(), "1m", limit)
