"""
Investor data mode + diagnostics helpers (demo vs live, last successful pulls).
"""

from __future__ import annotations

from datetime import timezone, datetime
from typing import Any
import os


def utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ensure_investor_data_health(app_state: Any) -> dict[str, Any]:
    existing = getattr(app_state, "investor_data_health", None)
    if not isinstance(existing, dict):
        h = {
            "price_last_success_utc": None,
            "chart_last_success_utc": None,
            "news_last_success_utc": None,
            "price_last_error": None,
            "profile_last_error": None,
            "chart_last_error": None,
            "news_last_error": None,
        }
        setattr(app_state, "investor_data_health", h)
        return h
    for k in (
        "price_last_success_utc",
        "chart_last_success_utc",
        "news_last_success_utc",
        "price_last_error",
        "profile_last_error",
        "chart_last_error",
        "news_last_error",
    ):
        if k not in existing:
            existing[k] = None
    return existing


def investor_provider_error_touch(
    app_state: Any,
    *,
    price: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    chart: dict[str, Any] | None = None,
    news: dict[str, Any] | None = None,
) -> None:
    """Persist last provider-visible failure for diagnostics (cleared on successful pulls)."""
    h = ensure_investor_data_health(app_state)
    stamp = utc_now_iso_z()
    def _merge(blob: dict[str, Any]) -> dict[str, Any]:
        out = {"recorded_utc": stamp, **blob}
        msg = out.get("message")
        if isinstance(msg, str):
            out["message"] = msg.strip() or "Provider error"
        det = out.get("detail")
        out["detail"] = (det.strip() if isinstance(det, str) else "") or None
        return out

    if price is not None:
        h["price_last_error"] = _merge(price)
    if profile is not None:
        h["profile_last_error"] = _merge(profile)
    if chart is not None:
        h["chart_last_error"] = _merge(chart)
    if news is not None:
        h["news_last_error"] = _merge(news)


def investor_provider_error_clear(app_state: Any, *, price: bool = False, profile: bool = False, chart: bool = False, news: bool = False) -> None:
    h = ensure_investor_data_health(app_state)
    if price:
        h["price_last_error"] = None
    if profile:
        h["profile_last_error"] = None
    if chart:
        h["chart_last_error"] = None
    if news:
        h["news_last_error"] = None


def investor_data_health_touch(app_state: Any, *, price: bool = False, chart: bool = False, news: bool = False) -> None:
    h = ensure_investor_data_health(app_state)
    now = utc_now_iso_z()
    if chart:
        h["chart_last_success_utc"] = now
    if price:
        h["price_last_success_utc"] = now
    if news:
        h["news_last_success_utc"] = now


_MARKET_LIVE_IDS = frozenset({"finnhub_market", "twelve_data_market"})
_NEWS_LIVE_IDS = frozenset({"finnhub_news", "alpha_vantage_news", "newsapi"})


def _market_channel(market_prov: Any) -> dict[str, Any]:
    name = getattr(market_prov, "name", "unknown")
    if name == "twelve_data_market":
        return {
            "role": "price_quotes_profiles",
            "provider_id": name,
            "status": "live",
            "detail": "Twelve Data API (symbol search, quotes, charts, profile metadata)",
        }
    if name == "finnhub_market":
        return {
            "role": "price_quotes_profiles",
            "provider_id": name,
            "status": "live",
            "detail": "Finnhub API (quotes, candles, profile). Optional TWELVE_DATA_API_KEY retries candles if Finnhub has no data.",
        }
    if name == "demo_market":
        return {
            "role": "price_quotes_profiles",
            "provider_id": name,
            "status": "demo",
            "detail": "Synthetic prices & series — RAGX_INVESTOR_MARKET_DEMO is set",
        }
    if name == "missing_api_key_market":
        return {
            "role": "price_quotes_profiles",
            "provider_id": name,
            "status": "missing_keys",
            "detail": "No market API key detected. Set TWELVE_DATA_API_KEY or FINNHUB_API_KEY (or RAGX_INVESTOR_MARKET_DEMO=1 for demo only).",
        }
    return {"role": "price_quotes_profiles", "provider_id": name, "status": "unknown", "detail": "Unrecognized investor market provider"}


def _chart_channel(market_prov: Any) -> dict[str, Any]:
    """Investor charts use the same candle gateway as quotes (Finnhub in live mode)."""
    base = _market_channel(market_prov)
    if base["status"] == "live":
        base = {
            **base,
            "role": "price_charts_timeseries",
            "detail": base.get("detail") or "Finnhub candles with optional Twelve Data fallback",
        }
    else:
        base = {**base, "role": "price_charts_timeseries"}
    return base


def _news_channel(news_prov: Any) -> dict[str, Any]:
    name = getattr(news_prov, "name", "unknown")
    if name == "finnhub_news":
        return {"role": "headlines", "provider_id": name, "status": "live", "detail": "Finnhub company news"}
    if name == "alpha_vantage_news":
        return {"role": "headlines", "provider_id": name, "status": "live", "detail": "Alpha Vantage NEWS_SENTIMENT"}
    if name == "newsapi":
        return {"role": "headlines", "provider_id": name, "status": "live", "detail": "NewsAPI.org everything search"}
    if name == "mock_news":
        return {
            "role": "headlines",
            "provider_id": name,
            "status": "mock",
            "detail": "RAGX_NEWS_PROVIDER=mock — no outbound headlines",
        }
    if name == "missing_news_api_key":
        return {
            "role": "headlines",
            "provider_id": name,
            "status": "missing_keys",
            "detail": "News API key missing. Set FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, NEWSAPI_API_KEY, or NEWS_API_KEY.",
        }
    return {"role": "headlines", "provider_id": name, "status": "unknown", "detail": "Unrecognized investor news provider"}


def _error_block(h: dict[str, Any], key: str) -> dict[str, Any] | None:
    raw = h.get(key)
    return raw if isinstance(raw, dict) else None


def build_investor_diagnostics(market_prov: Any, news_prov: Any, health: dict[str, Any] | None) -> dict[str, Any]:
    h = dict(health or {})
    mn = getattr(market_prov, "name", "")
    nn = getattr(news_prov, "name", "")
    live_market = mn in _MARKET_LIVE_IDS
    live_news = nn in _NEWS_LIVE_IDS
    badge_variant = "live" if (live_market and live_news) else "demo"
    badge_label = (
        "Live Market Data Connected"
        if badge_variant == "live"
        else "Demo Mode / Missing API Keys"
    )

    price_status = _market_channel(market_prov)
    chart_status = _chart_channel(market_prov)

    def _disp(ts: Any) -> str:
        return str(ts) if ts else "Never (no successful pull recorded yet)"

    return {
        "server_time_utc": utc_now_iso_z(),
        "badge_variant": badge_variant,
        "badge_label": badge_label,
        "fully_live": badge_variant == "live",
        "api_keys": {
            "FINNHUB_API_KEY": bool((os.getenv("FINNHUB_API_KEY") or "").strip()),
            "TWELVE_DATA_API_KEY": bool((os.getenv("TWELVE_DATA_API_KEY") or "").strip()),
            "RAGX_INVESTOR_MARKET_PROVIDER": (os.getenv("RAGX_INVESTOR_MARKET_PROVIDER") or "auto").strip() or "auto",
            # News keys are provider-dependent. We still report presence for common env vars.
            "ALPHA_VANTAGE_API_KEY": bool((os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip()),
            "NEWSAPI_API_KEY": bool((os.getenv("NEWSAPI_API_KEY") or "").strip() or (os.getenv("NEWS_API_KEY") or "").strip()),
            "news_provider_env_key": getattr(news_prov, "env_key", None),
        },
        "price_provider": {
            **price_status,
            "last_success_fetch_utc": _disp(h.get("price_last_success_utc")),
            "last_error": _error_block(h, "price_last_error"),
            "last_profile_error": _error_block(h, "profile_last_error"),
        },
        "chart_provider": {
            **chart_status,
            "last_success_fetch_utc": _disp(h.get("chart_last_success_utc")),
            "last_error": _error_block(h, "chart_last_error"),
        },
        "news_provider": {
            **_news_channel(news_prov),
            "last_success_fetch_utc": _disp(h.get("news_last_success_utc")),
            "last_error": _error_block(h, "news_last_error"),
        },
        "health_raw": {
            "price_last_success_utc": h.get("price_last_success_utc"),
            "chart_last_success_utc": h.get("chart_last_success_utc"),
            "news_last_success_utc": h.get("news_last_success_utc"),
            "profile_last_error": h.get("profile_last_error"),
        },
    }
