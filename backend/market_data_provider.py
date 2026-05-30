"""
Investor market data provider layer (search / quote / time-series / profile).

- Real quotes / closing-price history / profiles via Finnhub or Twelve Data (provider-selectable).
- Finnhub chart history falls back to Twelve Data when configured (Finnhub free tier may block OHLC endpoints).
- Optional deterministic demo prices: RAGX_INVESTOR_MARKET_DEMO=1 (synthetic series only in demo).
- Without a key (and demo off): explicit errors — no fabricated OHLC in live mode.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.investor_universe import get_universe_item, search_stock_universe

logger = logging.getLogger(__name__)

MSG_MARKET_KEY_MISSING = (
    "Investor market API unavailable: set TWELVE_DATA_API_KEY or FINNHUB_API_KEY (see diagnostics)."
)
MSG_TWELVE_CHART_PLAN_LIMIT = "Historical chart unavailable on current API plan."

# In-process cache + pacing for Twelve Data (Basic plan ≈ 8 credits/min).
_TWELVE_TS_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_TWELVE_CALL_TIMESTAMPS: list[float] = []

_TWELVE_RANGE_PLAN: dict[str, tuple[str, int]] = {
    "1D": ("15min", 160),
    "5D": ("1h", 120),
    "1M": ("1day", 45),
    "6M": ("1day", 190),
    "1Y": ("1week", 60),
}

_TWELVE_EXCHANGE_ALIASES: dict[str, str] = {
    "NYSE ARCA": "NYSE",
    "NYSE Arca": "NYSE",
    "NYSEARCA": "NYSE",
    "AMEX": "NYSE",
}


def _twelve_redact_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        qs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        safe = [(k, "***" if k.lower() == "apikey" else v) for k, v in qs]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(safe), ""))
    except Exception:
        return "<twelve-data-url>"


def _twelve_max_calls_per_minute() -> int:
    raw = (os.getenv("TWELVE_DATA_MAX_CALLS_PER_MINUTE") or "7").strip()
    try:
        n = int(raw)
    except ValueError:
        n = 7
    return max(1, min(n, 30))


def _twelve_cache_ttl_sec() -> float:
    raw = (os.getenv("TWELVE_DATA_CACHE_TTL_SEC") or "300").strip()
    try:
        n = float(raw)
    except ValueError:
        n = 300.0
    return max(30.0, min(n, 3600.0))


def _twelve_wait_for_rate_slot() -> None:
    """Pace outbound Twelve Data calls to stay under per-minute credit limits."""
    limit = _twelve_max_calls_per_minute()
    now = time.monotonic()
    global _TWELVE_CALL_TIMESTAMPS
    _TWELVE_CALL_TIMESTAMPS = [t for t in _TWELVE_CALL_TIMESTAMPS if now - t < 60.0]
    if len(_TWELVE_CALL_TIMESTAMPS) < limit:
        return
    sleep_for = 60.0 - (now - _TWELVE_CALL_TIMESTAMPS[0]) + 0.35
    if sleep_for > 0:
        logger.info(
            "Twelve Data pacing: sleeping %.1fs (%s calls in last 60s, limit %s/min)",
            sleep_for,
            len(_TWELVE_CALL_TIMESTAMPS),
            limit,
        )
        time.sleep(sleep_for)
        now = time.monotonic()
        _TWELVE_CALL_TIMESTAMPS = [t for t in _TWELVE_CALL_TIMESTAMPS if now - t < 60.0]


def _twelve_record_call() -> None:
    _TWELVE_CALL_TIMESTAMPS.append(time.monotonic())


def _twelve_plan_or_rate_limit(status: int | None, raw_body: str | None, data: Any | None) -> bool:
    if status in (403, 429):
        return True
    blob = " ".join(
        str(x or "")
        for x in (
            raw_body,
            (data or {}).get("message") if isinstance(data, dict) else "",
            (data or {}).get("code") if isinstance(data, dict) else "",
        )
    ).lower()
    return any(
        token in blob
        for token in (
            "rate limit",
            "api credit",
            "credit limit",
            "run out of api",
            "too many requests",
            "upgrade",
            "not available on your plan",
            "plan does not",
        )
    )


def _twelve_error_payload(
    sym: str,
    rng: str,
    td_interval: str,
    *,
    message: str,
    debug_detail: str | None = None,
    plan_limited: bool = False,
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "symbol": sym,
        "interval": rng,
        "points": [],
        "resolution": td_interval,
        "provider": "twelve_data_chart",
        "error": True,
        "message": message,
        "demo_mode": False,
        "debug_detail": debug_detail,
        "data_source": "twelve_data",
        "plan_limited": plan_limited,
        "http_status": http_status,
    }


def _twelve_log_failure(
    sym: str,
    rng: str,
    url: str,
    status: int | None,
    raw_body: str | None,
    data: Any | None,
) -> None:
    body_snip = (raw_body or "")[:700]
    if isinstance(data, dict) and not body_snip:
        body_snip = json.dumps({k: data.get(k) for k in ("status", "code", "message") if k in data})[:700]
    logger.warning(
        "Twelve Data time_series failed symbol=%s range=%s http_status=%s url=%s response=%s",
        sym,
        rng,
        status,
        _twelve_redact_url(url),
        body_snip or "<empty>",
    )


def _twelve_query_params(sym: str, td_interval: str, out_size: int, api_key: str) -> dict[str, str]:
    params: dict[str, str] = {
        "symbol": sym,
        "interval": td_interval,
        "outputsize": str(out_size),
        "apikey": api_key.strip(),
        "timezone": "UTC",
    }
    item = get_universe_item(sym) or {}
    exch_raw = str(item.get("exchange") or "").strip()
    if exch_raw:
        params["exchange"] = _TWELVE_EXCHANGE_ALIASES.get(exch_raw, exch_raw)
    return params


def _http_get_json(url: str, timeout: float = 15.0) -> tuple[Any | None, int | None, str | None]:
    req = urllib.request.Request(url, headers={"User-Agent": "RAGX-Trader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw), int(resp.getcode()), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        try:
            parsed = json.loads(body) if body.strip().startswith("{") else None
        except json.JSONDecodeError:
            parsed = None
        return parsed, e.code, body
    except urllib.error.URLError as e:
        return None, None, str(e.reason) if getattr(e, "reason", None) else str(e)
    except (TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        return None, None, str(e)


def _twelve_parse_bar_time(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        chunk = s[:n]
        try:
            dt = datetime.strptime(chunk, fmt).replace(tzinfo=UTC)
            return int(dt.timestamp())
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp())
    except ValueError:
        return None


def _twelve_data_time_series(api_key: str, symbol: str, interval_rng: str) -> dict[str, Any]:
    """
    Twelve Data time_series — primary Investor chart source when TWELVE_DATA_API_KEY is set.
    Endpoint: GET https://api.twelvedata.com/time_series
    """
    sym = symbol.upper().strip()
    rng = (interval_rng or "1M").upper().strip()
    td_interval, out_size = _TWELVE_RANGE_PLAN.get(rng, _TWELVE_RANGE_PLAN["6M"])

    cache_key = (sym, rng)
    cached = _TWELVE_TS_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return dict(cached[1])

    params = _twelve_query_params(sym, td_interval, out_size, api_key)
    url = f"https://api.twelvedata.com/time_series?{urllib.parse.urlencode(params)}"

    data: Any | None = None
    status: int | None = None
    raw_body: str | None = None
    max_attempts = 3
    for attempt in range(max_attempts):
        _twelve_wait_for_rate_slot()
        data, status, raw_body = _http_get_json(url, timeout=22.0)
        _twelve_record_call()
        if status == 429 and attempt < max_attempts - 1:
            wait_s = min(62.0, 2.5 * (attempt + 1))
            logger.warning(
                "Twelve Data rate limited (429) for %s %s; retry %s/%s in %.1fs url=%s",
                sym,
                rng,
                attempt + 2,
                max_attempts,
                wait_s,
                _twelve_redact_url(url),
            )
            time.sleep(wait_s)
            continue
        break

    if isinstance(status, int) and status >= 400:
        _twelve_log_failure(sym, rng, url, status, raw_body, data)
        if status == 401:
            detail = (raw_body or "")[:900]
            if isinstance(data, dict):
                detail = str(data.get("message") or detail)[:900]
            return _twelve_error_payload(
                sym,
                rng,
                td_interval,
                message="Twelve Data API key rejected.",
                debug_detail=detail or None,
                plan_limited=False,
                http_status=status,
            )
        plan_limited = _twelve_plan_or_rate_limit(status, raw_body, data)
        msg = MSG_TWELVE_CHART_PLAN_LIMIT if plan_limited else "Twelve Data HTTP error while loading closing prices."
        detail = (raw_body or "")[:900]
        if isinstance(data, dict):
            detail = str(data.get("message") or data.get("code") or detail)[:900]
        return _twelve_error_payload(
            sym,
            rng,
            td_interval,
            message=msg,
            debug_detail=detail or None,
            plan_limited=plan_limited,
            http_status=status,
        )

    if not isinstance(data, dict):
        _twelve_log_failure(sym, rng, url, status, raw_body, data)
        return _twelve_error_payload(
            sym,
            rng,
            td_interval,
            message="Twelve Data request failed.",
            debug_detail=raw_body[:900] if isinstance(raw_body, str) else str(raw_body),
        )

    if str(data.get("status") or "").lower() == "error":
        detail = str(data.get("message") or data.get("code") or data)
        _twelve_log_failure(sym, rng, url, status, raw_body, data)
        plan_limited = _twelve_plan_or_rate_limit(status, detail, data)
        msg = MSG_TWELVE_CHART_PLAN_LIMIT if plan_limited else f"Twelve Data: {detail}"
        return _twelve_error_payload(
            sym,
            rng,
            td_interval,
            message=msg,
            debug_detail=detail[:900],
            plan_limited=plan_limited,
            http_status=status,
        )

    meta = data.get("meta") or {}
    rows = data.get("values")
    if not isinstance(rows, list) or not rows:
        _twelve_log_failure(sym, rng, url, status, raw_body, data)
        return _twelve_error_payload(
            sym,
            rng,
            td_interval,
            message="Twelve Data returned no price rows for this range.",
            debug_detail=str(meta)[:500] if meta else None,
        )

    raw_points: list[dict[str, float | int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        dt_s = row.get("datetime")
        close_v = row.get("close") or row.get("last")
        if not isinstance(dt_s, str):
            continue
        ts = _twelve_parse_bar_time(dt_s)
        try:
            c = float(close_v)
        except (TypeError, ValueError):
            continue
        if ts is None:
            continue
        raw_points.append({"time": ts, "close": round(c, 4)})

    pts = normalize_close_series(raw_points)
    last_n = {"1D": 80, "5D": min(160, len(pts)), "1M": len(pts), "6M": len(pts), "1Y": len(pts)}.get(rng, len(pts))
    trimmed = pts[-last_n:] if len(pts) > last_n else pts
    if len(trimmed) < 2:
        _twelve_log_failure(sym, rng, url, status, raw_body, data)
        return _twelve_error_payload(
            sym,
            rng,
            td_interval,
            message="Twelve Data series too sparse after normalization.",
            debug_detail=json.dumps(meta)[:500] if meta else None,
        )

    result: dict[str, Any] = {
        "symbol": sym,
        "interval": rng,
        "resolution": td_interval,
        "points": trimmed,
        "provider": "twelve_data_chart",
        "error": False,
        "message": None,
        "demo_mode": False,
        "debug_detail": None,
        "data_source": "twelve_data",
        "currency": meta.get("currency") if isinstance(meta, dict) else None,
        "exchange": meta.get("exchange") if isinstance(meta, dict) else None,
    }
    _TWELVE_TS_CACHE[cache_key] = (time.time() + _twelve_cache_ttl_sec(), result)
    logger.info(
        "Twelve Data time_series ok symbol=%s range=%s interval=%s points=%s url=%s",
        sym,
        rng,
        td_interval,
        len(trimmed),
        _twelve_redact_url(url),
    )
    return result


def normalize_close_series(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unified shape for frontend + scorer: unix time + numeric close alias as price."""
    out: list[dict[str, Any]] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        t_raw = p.get("time") if "time" in p else p.get("t")
        c_raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            t = int(t_raw)
            c = float(c_raw)
        except (TypeError, ValueError):
            continue
        out.append({"time": t, "close": round(c, 4), "price": round(c, 4)})
    return sorted(out, key=lambda x: x["time"])


class MarketDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> dict[str, Any]:
        pass

    @abstractmethod
    def get_time_series(self, symbol: str, interval: str) -> dict[str, Any]:
        pass

    @abstractmethod
    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        pass

    def get_opportunity_fundamentals(self, symbol: str) -> dict[str, Any]:
        """Optional enrichment (market cap, sector, ranges) — default none."""
        return {}

    def get_insider_activity(self, symbol: str) -> dict[str, Any]:
        """SEC Form 4 insider transactions — Finnhub only; default empty."""
        from backend.investor_insider import empty_insider_payload

        return empty_insider_payload(
            symbol,
            self.name,
            reason="insider_requires_finnhub",
        )


class DemoMarketDataProvider(MarketDataProvider):
    """Synthetic OHLC-like series — only when RAGX_INVESTOR_MARKET_DEMO is enabled."""

    name = "demo_market"

    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        return list(search_stock_universe(query, limit=25))

    def get_quote(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        item = get_universe_item(sym) or {}
        return {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "price": None,
            "change_percent": None,
            "provider": self.name,
            "demo_mode": True,
            "error": False,
            "message": None,
        }

    def get_time_series(self, symbol: str, interval: str) -> dict[str, Any]:
        sym = symbol.upper()
        rng = (interval or "1M").upper()
        specs: dict[str, tuple[int, int, float]] = {
            "1D": (78, 5, 0.008),
            "5D": (65, 60, 0.012),
            "1M": (30, 24 * 60, 0.018),
            "6M": (26, 7 * 24 * 60, 0.035),
            "1Y": (52, 7 * 24 * 60, 0.05),
        }
        points_count, step_minutes, vol = specs.get(rng, specs["1M"])
        seed = abs(hash(f"{sym}:{rng}")) % (2**31)
        rnd = random.Random(seed)
        base = 90.0 + (seed % 180)
        now = datetime.now(UTC)
        t0 = now - timedelta(minutes=step_minutes * (points_count - 1))
        price = base
        raw_points: list[dict[str, float | int]] = []
        for i in range(points_count):
            drift = 0.0006
            shock = rnd.uniform(-vol, vol)
            price = max(5.0, price * (1.0 + drift + shock))
            ts = int((t0 + timedelta(minutes=i * step_minutes)).timestamp())
            raw_points.append({"time": ts, "close": round(price, 2)})
        pts = normalize_close_series(raw_points)
        return {
            "symbol": sym,
            "interval": rng,
            "points": pts,
            "provider": self.name,
            "demo_mode": True,
            "error": False,
            "message": None,
            "debug_detail": None,
        }

    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        item = get_universe_item(sym) or {}
        return {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "exchange": item.get("exchange"),
            "asset_type": item.get("asset_type"),
            "provider": self.name,
            "demo_mode": True,
            "error": False,
        }


class MissingApiKeyMarketDataProvider(MarketDataProvider):
    """No outbound market API — surfaces explicit UI messages."""

    name = "missing_api_key_market"

    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        return list(search_stock_universe(query, limit=25))

    def get_quote(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        item = get_universe_item(sym) or {}
        return {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "price": None,
            "change_percent": None,
            "provider": self.name,
            "error": True,
            "message": MSG_MARKET_KEY_MISSING,
        }

    def get_time_series(self, symbol: str, interval: str) -> dict[str, Any]:
        sym = symbol.upper()
        rng = (interval or "1M").upper()
        return {
            "symbol": sym,
            "interval": rng,
            "points": [],
            "provider": self.name,
            "error": True,
            "message": MSG_MARKET_KEY_MISSING,
            "demo_mode": False,
            "debug_detail": "Configure TWELVE_DATA_API_KEY or FINNHUB_API_KEY, or set RAGX_INVESTOR_MARKET_DEMO=1 for synthetic demo closing prices only.",
        }

    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        item = get_universe_item(sym) or {}
        return {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "exchange": item.get("exchange"),
            "asset_type": item.get("asset_type"),
            "provider": self.name,
            "error": True,
            "message": MSG_MARKET_KEY_MISSING,
        }


class TwelveDataMarketDataProvider(MarketDataProvider):
    name = "twelve_data_market"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    def _td_json(self, endpoint: str, params: dict[str, str], timeout: float = 16.0) -> tuple[dict[str, Any] | None, str | None]:
        q = {"apikey": self.api_key, **params}
        url = f"https://api.twelvedata.com/{endpoint}?{urllib.parse.urlencode(q)}"
        data, status, raw_body = _http_get_json(url, timeout=timeout)
        if isinstance(status, int) and status >= 400:
            return None, f"HTTP {status}: {(raw_body or '')[:320]}"
        if not isinstance(data, dict):
            return None, (raw_body or "")[:320] if isinstance(raw_body, str) else "bad_response"
        if str(data.get("status") or "").lower() == "error":
            return None, str(data.get("message") or data.get("code") or data)
        return data, None

    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        q = (query or "").strip()
        if not q:
            return []
        data, _err = self._td_json("symbol_search", {"symbol": q}, timeout=12.0)
        if not isinstance(data, dict):
            return []
        rows = data.get("data")
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in rows[:40]:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            type_raw = str(row.get("instrument_type") or row.get("type") or "").strip()
            company_name = str(row.get("instrument_name") or row.get("name") or "").strip() or None
            exch = str(row.get("exchange") or row.get("mic_code") or "").strip() or None
            asset_type = None
            t_up = type_raw.upper()
            if "ETF" in t_up:
                asset_type = "ETF"
            elif t_up in ("COMMON STOCK", "STOCK", "EQUITY", "SHARES"):
                asset_type = "Equity"
            elif t_up:
                asset_type = type_raw
            out.append(
                {
                    "ticker": sym,
                    "company_name": company_name,
                    "exchange": exch,
                    "type": type_raw or "Unknown",
                    "asset_type": asset_type,
                    "provider": self.name,
                }
            )
        return out

    def get_quote(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        item = get_universe_item(sym) or {}
        data, err = self._td_json("quote", {"symbol": sym}, timeout=13.0)
        if err or not isinstance(data, dict):
            return {
                "symbol": sym,
                "company_name": item.get("company_name"),
                "price": None,
                "change_percent": None,
                "provider": self.name,
                "error": True,
                "message": "Twelve Data quote request failed.",
                "debug_detail": err,
            }
        try:
            price_f = float(data.get("close")) if data.get("close") is not None else None
        except (TypeError, ValueError):
            price_f = None
        try:
            pct = float(data.get("percent_change")) if data.get("percent_change") is not None else None
        except (TypeError, ValueError):
            pct = None
        try:
            delta = float(data.get("change")) if data.get("change") is not None else None
        except (TypeError, ValueError):
            delta = None
        try:
            pc = float(data.get("previous_close")) if data.get("previous_close") is not None else None
        except (TypeError, ValueError):
            pc = None
        if delta is None and price_f is not None and pc is not None:
            delta = price_f - pc
        return {
            "symbol": sym,
            "company_name": str(data.get("name") or item.get("company_name") or "").strip() or item.get("company_name"),
            "price": price_f,
            "change_percent": pct,
            "change_dollar": delta,
            "previous_close": pc,
            "provider": self.name,
            "error": False,
            "message": None,
            "data_source": "twelve_data",
        }

    def get_time_series(self, symbol: str, interval: str) -> dict[str, Any]:
        data = _twelve_data_time_series(self.api_key, symbol, interval)
        if data.get("provider") == "twelve_data_chart":
            data["provider"] = self.name
        return data

    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        item = get_universe_item(sym) or {}
        base = {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "exchange": item.get("exchange"),
            "asset_type": item.get("asset_type") or "Equity",
            "provider": self.name,
            "error": False,
            "country": None,
            "currency": None,
            "ipo": None,
            "website": None,
            "logo": None,
            "data_source": "twelve_data",
        }
        quote, err_q = self._td_json("quote", {"symbol": sym}, timeout=13.0)
        if err_q:
            return {**base, "error": True, "message": "Twelve Data profile request failed.", "debug_detail": err_q}
        if isinstance(quote, dict):
            base["company_name"] = str(quote.get("name") or base.get("company_name") or "").strip() or base.get("company_name")
            base["exchange"] = str(quote.get("exchange") or base.get("exchange") or "").strip() or base.get("exchange")
            base["currency"] = quote.get("currency")
            t = str(quote.get("type") or "").strip()
            if "ETF" in t.upper():
                base["asset_type"] = "ETF"
            elif t:
                base["asset_type"] = "Equity" if "STOCK" in t.upper() else t
        return base


class FinnhubMarketDataProvider(MarketDataProvider):
    name = "finnhub_market"

    def __init__(self, api_key: str, twelve_data_api_key: str | None = None):
        self.api_key = api_key.strip()
        self.twelve_data_api_key = (twelve_data_api_key or "").strip() or None

    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        sym_query = (query or "").strip()
        if not sym_query:
            return []

        # Finnhub symbol search supports both tickers and company-name queries.
        # https://finnhub.io/docs/api#symbol-search
        params = urllib.parse.urlencode({"q": sym_query, "token": self.api_key})
        url = f"https://finnhub.io/api/v1/search?{params}"
        data, status, raw_body = _http_get_json(url, timeout=12.0)
        if isinstance(status, int) and status >= 400:
            return []
        if not isinstance(data, dict):
            return []
        results = data.get("result") or []
        if not isinstance(results, list) or not results:
            return []

        def _company_from_description(desc: str) -> str | None:
            # Example: "Apple Inc. (Common Stock)" -> "Apple Inc."
            d = desc.strip()
            if not d:
                return None
            if "(" in d:
                left = d.split("(", 1)[0].strip()
                if left:
                    return left
            return d

        out: list[dict[str, Any]] = []
        # Cap to keep UI snappy; this still covers "many" symbols via query.
        for row in results[:25]:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            desc = str(row.get("description") or "").strip()
            ftype = str(row.get("type") or "").strip()
            uni = get_universe_item(sym)
            company_name = _company_from_description(desc) or (uni.get("company_name") if uni else None)

            # Finnhub search doesn't reliably include exchange in the base response.
            # Fetch profile2 for the top few matches to populate exchange + asset_type.
            exchange = None
            asset_type = None
            if len(out) < 10:
                prof = self._finnhub_json("stock/profile2", {"symbol": sym})
                if isinstance(prof, dict):
                    exchange = str(prof.get("exchange") or prof.get("mic") or prof.get("market") or "").strip() or None
                    sec_type = str(prof.get("securityType") or "").strip()
                    if sec_type:
                        sec_up = sec_type.upper()
                        if "ETF" in sec_up:
                            asset_type = "ETF"
                        elif "FUND" in sec_up or "UNIT" in sec_up:
                            asset_type = sec_type
                        else:
                            asset_type = "Equity"

            # Provide a "type" field for the UI while keeping "asset_type" for existing logic.
            type_label = ftype or asset_type or "Unknown"

            out.append(
                {
                    "ticker": sym,
                    "company_name": company_name or None,
                    "exchange": exchange,
                    "type": type_label,
                    "asset_type": asset_type,
                    "provider": self.name,
                }
            )
        return out

    def _finnhub_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any] | list[Any] | None:
        q = {"token": self.api_key, **params}
        url = f"https://finnhub.io/api/v1/{endpoint}?{urllib.parse.urlencode(q)}"
        data, status, raw_body = _http_get_json(url, timeout=14.0)
        if isinstance(status, int) and status >= 400:
            logger.debug("Finnhub %s HTTP %s: %s", endpoint, status, (raw_body or "")[:280])
            return None
        if data is None:
            return None
        if isinstance(data, dict) and data.get("error"):
            logger.debug("Finnhub %s error field: %s", endpoint, data.get("error"))
            return None
        return data if isinstance(data, (dict, list)) else None

    def get_quote(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper()
        item = get_universe_item(sym) or {}
        params = urllib.parse.urlencode({"symbol": sym, "token": self.api_key})
        url = f"https://finnhub.io/api/v1/quote?{params}"
        data, status, raw_body = _http_get_json(url, timeout=12.0)
        if isinstance(status, int) and status >= 400:
            fb = ""
            if isinstance(data, dict):
                fb = str(data.get("error") or data)
            tail = fb or (raw_body or "")[:480]
            return {
                "symbol": sym,
                "company_name": item.get("company_name"),
                "price": None,
                "change_percent": None,
                "provider": self.name,
                "error": True,
                "message": f"Finnhub quote HTTP {status}",
                "debug_detail": tail,
            }
        if data is None:
            return {
                "symbol": sym,
                "company_name": item.get("company_name"),
                "price": None,
                "change_percent": None,
                "provider": self.name,
                "error": True,
                "message": "Could not reach Finnhub for a quote.",
                "debug_detail": (raw_body or "")[:480] if raw_body else None,
            }
        if not isinstance(data, dict):
            return {
                "symbol": sym,
                "company_name": item.get("company_name"),
                "price": None,
                "change_percent": None,
                "provider": self.name,
                "error": True,
                "message": "Finnhub returned an unreadable quote payload.",
                "debug_detail": str(data)[:480],
            }
        err_fh = data.get("error")
        if err_fh is not None:
            tail = json.dumps(data)[:480]
            return {
                "symbol": sym,
                "company_name": item.get("company_name"),
                "price": None,
                "change_percent": None,
                "provider": self.name,
                "error": True,
                "message": f"Finnhub: {err_fh}".strip(),
                "debug_detail": tail,
            }
        price = data.get("c")
        pct = data.get("dp")
        delta = data.get("d")
        pc = data.get("pc")
        chg_usd = float(delta) if delta is not None else None
        price_f = float(price) if price is not None else None
        pc_f = float(pc) if pc is not None else None
        if chg_usd is None and price_f is not None and pc_f is not None:
            chg_usd = price_f - pc_f
        return {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "price": price_f,
            "change_percent": float(pct) if pct is not None else None,
            "change_dollar": chg_usd,
            "previous_close": pc_f,
            "provider": self.name,
            "error": False,
            "message": None,
            "data_source": "finnhub",
        }

    def _finnhub_candles(self, sym: str, rng: str) -> dict[str, Any]:
        now = int(datetime.now(UTC).timestamp())
        horizon_sec = {"1D": 3 * 86400, "5D": 10 * 86400, "1M": 40 * 86400, "6M": 200 * 86400, "1Y": 400 * 86400}.get(
            rng, 40 * 86400
        )
        base_frm = max(0, now - horizon_sec)
        daily_widen = {"1D": 50 * 86400, "5D": 120 * 86400}
        resolve_attempts = ["60", "D"] if rng in ("1D", "5D") else ["D"]
        blob: dict[str, Any] = {}
        resolve = resolve_attempts[0]
        try:
            for resolve in resolve_attempts:
                frm = max(0, now - daily_widen[rng]) if resolve == "D" and rng in daily_widen else base_frm
                params = urllib.parse.urlencode(
                    {
                        "symbol": sym,
                        "resolution": resolve,
                        "from": frm,
                        "to": now,
                        "token": self.api_key,
                        "adjusted": "true",
                    }
                )
                url = f"https://finnhub.io/api/v1/stock/candle?{params}"
                data, status, raw_body = _http_get_json(url, timeout=15.0)
                if isinstance(status, int) and status >= 400:
                    fb = ""
                    if isinstance(data, dict):
                        fb = str(data.get("error") or "")
                    tail = fb or (raw_body or "")[:560]
                    if status in (401, 403) and resolve != resolve_attempts[-1]:
                        continue
                    return {
                        "symbol": sym,
                        "interval": rng,
                        "points": [],
                        "resolution": resolve,
                        "provider": self.name,
                        "error": True,
                        "message": f"Finnhub historical closes unavailable (HTTP {status}).",
                        "demo_mode": False,
                        "debug_detail": tail,
                        "data_source": "finnhub",
                    }
                if not isinstance(data, dict):
                    return {
                        "symbol": sym,
                        "interval": rng,
                        "points": [],
                        "resolution": resolve,
                        "provider": self.name,
                        "error": True,
                        "message": "Finnhub returned an unreadable historical price payload.",
                        "demo_mode": False,
                        "debug_detail": (raw_body or "")[:560] if raw_body else None,
                        "data_source": "finnhub",
                    }
                blob = data
                if isinstance(blob, dict) and blob.get("error"):
                    bd = json.dumps(blob)[:560]
                    return {
                        "symbol": sym,
                        "interval": rng,
                        "points": [],
                        "resolution": resolve,
                        "provider": self.name,
                        "error": True,
                        "message": f"Finnhub: {blob.get('error')}",
                        "demo_mode": False,
                        "debug_detail": bd,
                        "data_source": "finnhub",
                    }
                st = blob.get("s")
                if st == "ok":
                    break
                if st == "no_data" and resolve == "60" and rng in ("1D", "5D"):
                    continue
                msg = (
                    "No closing prices returned for this symbol/interval."
                    if st == "no_data"
                    else "Finnhub returned unexpected historical price status."
                )
                return {
                    "symbol": sym,
                    "interval": rng,
                    "points": [],
                    "resolution": resolve,
                    "provider": self.name,
                    "error": True,
                    "message": msg,
                    "demo_mode": False,
                    "debug_detail": json.dumps(blob)[:560] if blob else repr(st),
                    "data_source": "finnhub",
                }

            ts_list = blob.get("t") or []
            closes = blob.get("c") or []
            raw = [{"time": int(ts_list[i]), "close": float(closes[i])} for i in range(min(len(ts_list), len(closes)))]
            pts = normalize_close_series(raw)
            last_n = {"1D": 80, "5D": min(160, len(pts)), "1M": len(pts), "6M": len(pts), "1Y": len(pts)}.get(
                rng, len(pts)
            )
            trimmed = pts[-last_n:] if len(pts) > last_n else pts
            return {
                "symbol": sym,
                "interval": rng,
                "resolution": resolve,
                "points": trimmed,
                "provider": self.name,
                "error": len(trimmed) < 2,
                "message": None if len(trimmed) >= 2 else "Finnhub returned fewer than two closes for this range.",
                "demo_mode": False,
                "debug_detail": json.dumps(blob)[:560] if len(trimmed) < 2 and blob else None,
                "data_source": "finnhub",
            }
        except (ValueError, TypeError, KeyError, IndexError, OSError) as e:
            detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            logger.exception("Finnhub candle parse failed for %s", sym)
            return {
                "symbol": sym,
                "interval": rng,
                "points": [],
                "provider": self.name,
                "error": True,
                "message": "Market data request failed parsing Finnhub closing prices.",
                "demo_mode": False,
                "debug_detail": detail,
                "data_source": "finnhub",
            }

    def get_time_series(self, symbol: str, interval: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        rng = (interval or "1M").upper().strip()
        td_key = (self.twelve_data_api_key or os.getenv("TWELVE_DATA_API_KEY", "").strip()) or None

        # When Twelve Data is configured, use it first (Finnhub free tier blocks candles with HTTP 403).
        if td_key:
            td = _twelve_data_time_series(td_key, sym, rng)
            td["provider"] = self.name
            if not td.get("error") and len(td.get("points") or []) >= 2:
                td["data_source"] = "twelve_data"
                return td
            return td

        fh = self._finnhub_candles(sym, rng)
        fh_ok = not fh.get("error") and len(fh.get("points") or []) >= 2
        if fh_ok:
            fh["demo_mode"] = False
            if "data_source" not in fh:
                fh["data_source"] = "finnhub"
            return fh
        detail = str(fh.get("debug_detail") or fh.get("message") or "")
        if fh.get("error") and ("403" in detail or "403" in str(fh.get("message") or "")):
            fh["message"] = "Historical chart requires Twelve Data API key."
        fh["demo_mode"] = False
        if "message" not in fh or fh.get("message") is None:
            fh["message"] = "Closing-price history unavailable for this symbol/range."
        return fh

    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        item = get_universe_item(sym) or {}
        url = (
            "https://finnhub.io/api/v1/stock/profile2?"
            + urllib.parse.urlencode({"symbol": sym, "token": self.api_key})
        )
        data, status, raw_body = _http_get_json(url, timeout=14.0)
        base = {
            "symbol": sym,
            "company_name": item.get("company_name"),
            "exchange": item.get("exchange"),
            "asset_type": item.get("asset_type") or "Equity",
            "provider": self.name,
            "error": False,
            "country": None,
            "currency": None,
            "ipo": None,
            "website": None,
            "logo": None,
            "data_source": "finnhub",
        }
        if isinstance(status, int) and status >= 400:
            fb = ""
            if isinstance(data, dict):
                fb = str(data.get("error") or "")
            tail = fb or (raw_body or "")[:720]
            return {
                **base,
                "error": True,
                "message": f"Finnhub profile HTTP {status}",
                "debug_detail": tail,
            }
        if data is None or not isinstance(data, dict):
            return {
                **base,
                "error": True,
                "message": "Could not load company profile from Finnhub.",
                "debug_detail": (raw_body or "")[:720] if raw_body else None,
            }
        if not str(data.get("name") or "").strip() and not str(data.get("ticker") or "").strip():
            return {
                **base,
                "error": True,
                "message": "Finnhub did not return metadata for this symbol.",
                "debug_detail": json.dumps(data)[:720],
            }
        nm = str(data.get("name") or data.get("ticker") or "").strip()
        exch = str(data.get("exchange") or "").strip()
        mkt = str(data.get("market") or "").strip()
        mic = str(data.get("mic") or "").strip()
        base["company_name"] = nm or base["company_name"]
        base["exchange"] = exch or mic or mkt or base["exchange"]
        base["currency"] = data.get("currency")
        base["country"] = data.get("country")
        base["ipo"] = data.get("ipo")
        base["website"] = data.get("weburl")
        base["logo"] = data.get("logo")
        if str(data.get("securityType") or "").strip():
            base["security_type"] = str(data["securityType"]).strip()
            if "ETF" in base["security_type"].upper():
                base["asset_type"] = "ETF"
            elif "FUNDS" in base["security_type"].upper():
                base["asset_type"] = base["security_type"]
        return base

    def get_opportunity_fundamentals(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        out: dict[str, Any] = {
            "symbol": sym,
            "provider": self.name,
            "market_cap_usd": None,
            "sector": None,
            "week52_high": None,
            "week52_low": None,
            "annual_volatility_pct": None,
            "annualized_beta": None,
        }
        p2 = self._finnhub_json("stock/profile2", {"symbol": sym})
        if isinstance(p2, dict):
            mc_m = p2.get("marketCapitalization")
            if isinstance(mc_m, (int, float)) and mc_m > 0:
                out["market_cap_usd"] = float(mc_m) * 1_000_000.0
            ind = (
                str(p2.get("finnhubIndustry") or "").strip()
                or str(p2.get("gsector") or "").strip()
                or str(p2.get("gind") or "").strip()
            )
            if ind:
                out["sector"] = ind[:120]
            if not out.get("sector") and p2.get("name"):
                exch = str(p2.get("exchange") or "").strip()
                if exch:
                    out["sector"] = f"{exch} Equity"[:120]

        met_raw = self._finnhub_json("stock/metric", {"symbol": sym, "metric": "all"})
        md = (met_raw or {}).get("metric") if isinstance(met_raw, dict) else {}
        if isinstance(md, dict):
            hi = md.get("52WeekHigh")
            lo = md.get("52WeekLow")
            vol = md.get("annualVolatility") or md.get("volatilityDay")
            beta = md.get("beta") or md.get("10DayBeta")
            try:
                if hi is not None:
                    out["week52_high"] = float(hi)
                if lo is not None:
                    out["week52_low"] = float(lo)
                if vol is not None:
                    out["annual_volatility_pct"] = float(vol)
                if beta is not None:
                    out["annualized_beta"] = float(beta)
            except (TypeError, ValueError):
                pass

        return out

    def get_insider_activity(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        end = datetime.now(UTC).date()
        start = end - timedelta(days=365)
        raw = self._finnhub_json(
            "stock/insider-transactions",
            {
                "symbol": sym,
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        )
        from backend.investor_insider import build_insider_activity_payload

        return build_insider_activity_payload(sym, self.name, raw, finnhub_available=True)


def create_market_data_provider() -> MarketDataProvider:
    demo = os.getenv("RAGX_INVESTOR_MARKET_DEMO", "").strip().lower() in ("1", "true", "yes")
    if demo:
        logger.warning("Investor market: DEMO MODE (synthetic prices) enabled via RAGX_INVESTOR_MARKET_DEMO.")
        return DemoMarketDataProvider()
    mode = os.getenv("RAGX_INVESTOR_MARKET_PROVIDER", "auto").strip().lower()
    twelve = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    hub = os.getenv("FINNHUB_API_KEY", "").strip()

    if mode in ("twelve", "twelve_data"):
        if twelve:
            logger.info("Investor market provider: twelve_data_market (explicit)")
            return TwelveDataMarketDataProvider(twelve)
        logger.warning("RAGX_INVESTOR_MARKET_PROVIDER=%s but TWELVE_DATA_API_KEY is missing.", mode)
    elif mode == "finnhub":
        if hub:
            logger.info("Investor market provider: finnhub_market (explicit)")
            return FinnhubMarketDataProvider(hub, twelve_data_api_key=twelve or None)
        logger.warning("RAGX_INVESTOR_MARKET_PROVIDER=finnhub but FINNHUB_API_KEY is missing.")
    elif mode not in ("", "auto"):
        logger.warning("Unknown RAGX_INVESTOR_MARKET_PROVIDER=%r. Falling back to auto provider selection.", mode)

    # Auto: Finnhub when available (quotes, profile, fundamentals); Twelve optional for closing-price charts.
    if hub:
        logger.info(
            "Investor market provider: finnhub_market (auto%s)",
            ", twelve closing-price fallback" if twelve else "",
        )
        return FinnhubMarketDataProvider(hub, twelve_data_api_key=twelve or None)
    if twelve:
        logger.info("Investor market provider: twelve_data_market (auto, finnhub unavailable)")
        return TwelveDataMarketDataProvider(twelve)

    legacy = os.getenv("RAGX_MARKET_DATA_PROVIDER", "").strip().lower()
    if legacy and legacy != "mock":
        logger.warning(
            "RAGX_MARKET_DATA_PROVIDER=%r is unsupported; configure TWELVE_DATA_API_KEY or FINNHUB_API_KEY (or demo mode).",
            legacy,
        )
    return MissingApiKeyMarketDataProvider()
