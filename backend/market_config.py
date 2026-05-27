"""
Single source of truth for the dashboard market identity (RAGX-Trader).

All live prices, candles, REST history, WebSocket klines, strategy evaluation, and logs
use the same Binance **Spot** REST/WS endpoints selected in `backend.config` plus the
active symbol from `SymbolManager`.

Defaults: **Binance Global Spot**, symbol **BTCUSDT**. Override region only with env
`RAGX_BINANCE_REGION` (see `backend.config`). There is **no** futures / perp feed.

`validate_dashboard_market_or_die()` runs after `configure_binance()` so the process
refuses to start if resolved endpoints are not the known Spot pair for the region
or if URLs contain forbidden venue fragments (futures, CME, etc.).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Canonical identifiers (API + UI)
EXCHANGE_ID = "binance"
EXCHANGE_LABEL = "Binance"
INSTRUMENT_TYPE = "spot_crypto"
INSTRUMENT_LABEL = "Spot crypto"
INSTRUMENT_CATEGORY = "spot"
MARGINED_FUTURES = False
DEFAULT_SPOT_SYMBOL = "BTCUSDT"

# If any of these appear in REST/WS base URLs, refuse startup (wrong product / venue).
_FORBIDDEN_ENDPOINT_FRAGMENTS = (
    "fapi",
    "dapi",
    "future",
    "futures",
    "delivery",
    "cmegroup",
    "interactivebrokers",
    "deribit",
    "bybit",
    "swap",
    "perp",
    "perpetual",
    "btc1!",
    "eth1!",
)


def validate_dashboard_market_or_die() -> None:
    """
    Strict guard: active `config.rest_base()` / `ws_base_root()` must match the canonical
    Binance **Spot** endpoints for `effective_region`, and must not look like futures or
    unrelated exchanges. Call immediately after `configure_binance()`.
    """
    from backend import config

    region = config.effective_region
    exp_rest, exp_ws = config.canonical_spot_endpoints(region)
    got_rest = config.rest_base().rstrip("/")
    got_ws = config.ws_base_root().rstrip("/")
    exp_rest_n = exp_rest.rstrip("/")
    exp_ws_n = exp_ws.rstrip("/")

    if got_rest != exp_rest_n:
        msg = (
            f"Market integrity: REST base {got_rest!r} does not match required "
            f"Binance Spot ({region}) {exp_rest_n!r}. Refusing startup."
        )
        logger.error(msg)
        raise RuntimeError(msg)
    if got_ws != exp_ws_n:
        msg = (
            f"Market integrity: WebSocket base {got_ws!r} does not match required "
            f"Binance Spot ({region}) {exp_ws_n!r}. Refusing startup."
        )
        logger.error(msg)
        raise RuntimeError(msg)

    combined = (got_rest + " " + got_ws).lower()
    for frag in _FORBIDDEN_ENDPOINT_FRAGMENTS:
        if frag in combined:
            msg = (
                f"Market integrity: endpoint URLs contain forbidden fragment {frag!r} "
                "(not Binance Spot). Refusing startup."
            )
            logger.error(msg)
            raise RuntimeError(msg)

    logger.info(
        "Market integrity OK: Binance Spot (%s), REST=%s WS=%s",
        region,
        got_rest,
        got_ws,
    )


def build_api_config_payload(
    *,
    binance_region: str,
    symbol: str,
    interval: str,
) -> dict[str, Any]:
    """
    Payload for GET /api/config — the browser treats this as the active market contract.

    Requires `configure_binance()` to have run so REST/WS bases match `effective_region`.
    """
    from backend import config

    sym = str(symbol or DEFAULT_SPOT_SYMBOL).strip().upper() or DEFAULT_SPOT_SYMBOL
    iv = str(interval or "1m").strip() or "1m"
    region = binance_region if binance_region in ("com", "us") else "com"

    venue = "Global" if region == "com" else "US"
    ui_feed_label = f"Binance Spot ({venue})"
    ui_market_line = f"Market: {sym} · {ui_feed_label}"
    market_badge = f"Binance Spot ({venue}) • {sym}"

    return {
        "exchange": EXCHANGE_ID,
        "exchange_label": EXCHANGE_LABEL,
        "instrument_type": INSTRUMENT_TYPE,
        "instrument_label": INSTRUMENT_LABEL,
        "instrument_category": INSTRUMENT_CATEGORY,
        "margined_futures": MARGINED_FUTURES,
        "symbol": sym,
        "interval": iv,
        "binance_region": region,
        "rest_base_url": config.rest_base(),
        "ws_base_url": config.ws_base_root(),
        "ui_feed_label": ui_feed_label,
        "ui_market_line": ui_market_line,
        "market_badge": market_badge,
    }
