"""
Runtime configuration (RAGX-Trader)

All dashboard data uses **Binance Spot** REST + WebSocket (no futures / perp endpoints).

- **RAGX_BINANCE_REGION=com** (default): Binance Global Spot only (`api.binance.com`).
- **RAGX_BINANCE_REGION=us**: Binance.US Spot only — use when you explicitly want US books.
- **RAGX_BINANCE_REGION=auto**: opt-in behavior — try Global; if ping returns **HTTP 451**,
  use Binance.US. Not the default, so there is no silent switch away from Global.

Unknown values fall back to **com** (Global).
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from typing import Literal

logger = logging.getLogger(__name__)

BinanceRegion = Literal["com", "us"]

# Spot-only hosts (validated at startup — must not be mixed with futures / other venues).
_BINANCE_REST = {
    "com": "https://api.binance.com/api/v3",
    "us": "https://api.binance.us/api/v3",
}
_BINANCE_WS = {
    "com": "wss://stream.binance.com:9443/ws",
    "us": "wss://stream.binance.us:9443/ws",
}

# Filled by configure_binance() at app startup.
effective_region: BinanceRegion = "com"


def _env_region() -> str:
    return os.getenv("RAGX_BINANCE_REGION", "com").strip().lower()


def _com_reachable() -> bool:
    """False when Binance.com blocks the client (e.g. HTTP 451 in some regions)."""
    url = "https://api.binance.com/api/v3/ping"
    req = urllib.request.Request(url, headers={"User-Agent": "RAGX-Trader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read(64)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 451:
            logger.warning(
                "Binance Global returned HTTP 451 (not available from your network/region). "
                "Using Binance.US only because RAGX_BINANCE_REGION=auto."
            )
            return False
        logger.warning("Binance.com ping HTTP %s — still trying Global first.", e.code)
        return True
    except OSError as e:
        logger.warning("Binance.com ping failed (%s) — still trying Global first.", e)
        return True


def resolve_binance_region() -> BinanceRegion:
    """
    - **us**: Binance.US Spot only.
    - **com** (default): Binance Global Spot only.
    - **auto**: Global if reachable (non-451), else US — explicit opt-in only.
    """
    raw = _env_region()
    if raw == "us":
        return "us"
    if raw == "com" or raw == "":
        return "com"
    if raw == "auto":
        return "com" if _com_reachable() else "us"
    logger.warning("Unknown RAGX_BINANCE_REGION=%r — using com (Binance Global).", raw)
    return "com"


def configure_binance() -> BinanceRegion:
    """Call once during FastAPI startup."""
    global effective_region
    effective_region = resolve_binance_region()
    logger.info(
        "Binance data region: %s (REST %s, WS %s)",
        effective_region,
        rest_base(),
        _BINANCE_WS[effective_region],
    )
    return effective_region


def canonical_spot_endpoints(region: BinanceRegion) -> tuple[str, str]:
    """Expected REST + WebSocket bases for Binance **Spot** only (no futures)."""
    return _BINANCE_REST[region], _BINANCE_WS[region]


def rest_base() -> str:
    return _BINANCE_REST[effective_region]


def ws_base_root() -> str:
    """Host for Spot kline WebSocket streams (`/ws/<stream>` path built elsewhere)."""
    return _BINANCE_WS[effective_region]


def klines_url(symbol: str, interval: str, limit: int) -> str:
    sym = symbol.upper()
    return f"{rest_base()}/klines?symbol={sym}&interval={interval}&limit={limit}"


def ws_kline_url(symbol_interval_stream: str) -> str:
    """
    symbol_interval_stream e.g. 'btcusdt@kline_1m'
    """
    return f"{ws_base_root()}/{symbol_interval_stream}"
