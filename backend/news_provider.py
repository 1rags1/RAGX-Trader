"""
Investor news provider layer (company/ticker headlines only — never invented).

Providers (via RAGX_NEWS_PROVIDER):
  - auto (default): first available FINNHUB_API_KEY → ALPHA_VANTAGE_API_KEY → NEWSAPI_API_KEY
  - finnhub | alpha_vantage | newsapi: require that provider's env key
  - mock: empty feed, no error (offline / tests only)

Env keys:
  FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, NEWSAPI_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

MSG_NEWS_KEY_MISSING = "News API key missing."
MSG_NEWS_RATE_LIMIT = "News rate limit reached"
MSG_NEWS_FETCH_FAILED = "News API request failed."


def _ts_to_iso_utc(ts: int | float) -> str | None:
    """Finnhub often uses unix seconds; some feeds use ms."""
    try:
        t = float(ts)
        if t > 1e12:
            t = t / 1000.0
        return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError, TypeError):
        return None


def _normalize_article(candidate: dict[str, Any]) -> dict[str, Any] | None:
    headline = candidate.get("headline")
    if not isinstance(headline, str):
        headline = candidate.get("title")
    if not isinstance(headline, str) or not headline.strip():
        return None
    url = candidate.get("url")
    url_s = url.strip() if isinstance(url, str) else ""
    summary = candidate.get("summary")
    summary_s = summary.strip() if isinstance(summary, str) else ""

    pub = candidate.get("published_at")
    if not isinstance(pub, str):
        pub = None

    source = candidate.get("source")
    if isinstance(source, dict):
        src = source.get("name")
    elif isinstance(source, str):
        src = source
    else:
        src = None
    src_s = src.strip() if isinstance(src, str) and src.strip() else "Unknown"

    return {
        "headline": headline.strip(),
        "source": src_s,
        "published_at": pub,
        "summary": summary_s if summary_s else None,
        "url": url_s if url_s else None,
    }


def _av_looks_like_rate_limit(data: dict[str, Any]) -> bool:
    note = data.get("Note") or data.get("Information") or ""
    if not isinstance(note, str):
        return False
    low = note.lower()
    return "api call frequency" in low or "rate limit" in low or "25 requests per day" in low or "premium" in low and "endpoint" in low


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
        logger.exception("News URL error: %s", url[:80])
        return None, None, str(e.reason) if getattr(e, "reason", None) else str(e)
    except (TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        logger.exception("News fetch/decode failed: %s", url[:80])
        return None, None, str(e)


class NewsProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_company_news_result(self, symbol: str) -> dict[str, Any]:
        pass

    def get_company_news(self, symbol: str) -> list[dict[str, Any]]:
        payload = self.get_company_news_result(symbol.upper().strip())
        items = payload.get("items")
        return items if isinstance(items, list) else []


class MockNewsProvider(NewsProvider):
    """Explicit RAGX_NEWS_PROVIDER=mock — empty articles, no error (offline only)."""

    name = "mock_news"

    def get_company_news_result(self, symbol: str) -> dict[str, Any]:
        return {"items": [], "error": False, "message": None, "reason": None}


class MissingNewsApiKeyNewsProvider(NewsProvider):
    name = "missing_news_api_key"

    def get_company_news_result(self, symbol: str) -> dict[str, Any]:
        return {
            "items": [],
            "error": True,
            "message": MSG_NEWS_KEY_MISSING,
            "reason": "missing_key",
        }


class _KeyedNewsProviderBase(NewsProvider):
    env_key: str = ""
    name: str = "keyed_news"

    def __init__(self, api_key: str):
        self.api_key = api_key.strip()

    @classmethod
    def from_env(cls) -> "_KeyedNewsProviderBase | None":
        key = os.getenv(cls.env_key, "").strip()
        if not key:
            return None
        return cls(key)

    def _rate_limit_payload(self) -> dict[str, Any]:
        return {"items": [], "error": True, "message": MSG_NEWS_RATE_LIMIT, "reason": "rate_limit"}


class FinnhubNewsProvider(_KeyedNewsProviderBase):
    name = "finnhub_news"
    env_key = "FINNHUB_API_KEY"

    def get_company_news_result(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        end_dt = datetime.now(UTC).date()
        start_dt = end_dt - timedelta(days=60)
        params = urllib.parse.urlencode(
            {
                "symbol": sym,
                "from": start_dt.isoformat(),
                "to": end_dt.isoformat(),
                "token": self.api_key,
            }
        )
        url = f"https://finnhub.io/api/v1/company-news?{params}"
        data, status, raw_body = _http_get_json(url, timeout=18.0)
        if status == 429:
            return self._rate_limit_payload()
        if isinstance(status, int) and status >= 400:
            return {
                "items": [],
                "error": True,
                "message": MSG_NEWS_FETCH_FAILED,
                "reason": "http_error",
                "debug_detail": raw_body[:500] if raw_body else None,
            }
        if data is None:
            return {
                "items": [],
                "error": True,
                "message": MSG_NEWS_FETCH_FAILED,
                "reason": "network",
                "debug_detail": (raw_body or "")[:500] if raw_body else None,
            }

        if not isinstance(data, list):
            msg = MSG_NEWS_FETCH_FAILED
            if isinstance(data, dict) and data.get("error"):
                msg = str(data.get("error") or msg)
            return {"items": [], "error": True, "message": msg, "reason": "bad_response"}

        items: list[dict[str, Any]] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            ts = row.get("datetime")
            published = None
            if isinstance(ts, (int, float)):
                published = _ts_to_iso_utc(ts)
            cand = _normalize_article(
                {
                    "headline": row.get("headline"),
                    "source": row.get("source"),
                    "published_at": published,
                    "summary": row.get("summary"),
                    "url": row.get("url"),
                }
            )
            if cand:
                items.append(cand)
        return {"items": items, "error": False, "message": None, "reason": None}


class AlphaVantageNewsProvider(_KeyedNewsProviderBase):
    name = "alpha_vantage_news"
    env_key = "ALPHA_VANTAGE_API_KEY"

    def get_company_news_result(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        params = urllib.parse.urlencode(
            {
                "function": "NEWS_SENTIMENT",
                "tickers": sym,
                "sort": "LATEST",
                "limit": "25",
                "apikey": self.api_key,
            }
        )
        url = f"https://www.alphavantage.co/query?{params}"
        data, status, raw_body = _http_get_json(url, timeout=18.0)
        if status == 429:
            return self._rate_limit_payload()
        if isinstance(status, int) and status >= 400:
            return {
                "items": [],
                "error": True,
                "message": MSG_NEWS_FETCH_FAILED,
                "reason": "http_error",
                "debug_detail": (raw_body or "")[:500],
            }
        if not isinstance(data, dict):
            return {"items": [], "error": True, "message": MSG_NEWS_FETCH_FAILED, "reason": "bad_response"}

        feed = data.get("feed")
        if _av_looks_like_rate_limit(data) and (not isinstance(feed, list) or len(feed) == 0):
            return self._rate_limit_payload()

        if feed is None and data.get("Error Message"):
            return {
                "items": [],
                "error": True,
                "message": MSG_NEWS_FETCH_FAILED,
                "reason": "av_error",
            }
        if not isinstance(feed, list):
            return {"items": [], "error": True, "message": MSG_NEWS_FETCH_FAILED, "reason": "bad_response"}

        items: list[dict[str, Any]] = []
        for row in feed:
            if not isinstance(row, dict):
                continue
            raw_time = row.get("time_published")
            published = None
            if isinstance(raw_time, str) and raw_time:
                try:
                    dt = datetime.strptime(raw_time[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
                    published = dt.isoformat().replace("+00:00", "Z")
                except ValueError:
                    published = None
            cand = _normalize_article(
                {
                    "headline": row.get("title"),
                    "source": row.get("source"),
                    "published_at": published,
                    "summary": row.get("summary"),
                    "url": row.get("url"),
                }
            )
            if cand:
                items.append(cand)

        return {"items": items, "error": False, "message": None, "reason": None}


class NewsApiOrgProvider(_KeyedNewsProviderBase):
    """https://newsapi.org — ticker-oriented everything search."""

    name = "newsapi"
    env_key = "NEWSAPI_API_KEY"

    @classmethod
    def from_env(cls) -> NewsApiOrgProvider | None:
        key = (os.getenv("NEWSAPI_API_KEY") or os.getenv("NEWS_API_KEY") or "").strip()
        if not key:
            return None
        return cls(key)

    def get_company_news_result(self, symbol: str) -> dict[str, Any]:
        sym = symbol.upper().strip()
        q = f'"{sym}" OR {sym}'
        params = urllib.parse.urlencode(
            {
                "q": q,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": "30",
                "apiKey": self.api_key,
            }
        )
        url = f"https://newsapi.org/v2/everything?{params}"
        data, status, raw_body = _http_get_json(url, timeout=18.0)
        if status == 429:
            return self._rate_limit_payload()
        if isinstance(status, int) and status == 401:
            return {"items": [], "error": True, "message": MSG_NEWS_KEY_MISSING, "reason": "auth"}
        if isinstance(status, int) and status >= 400:
            code = ""
            msg = MSG_NEWS_FETCH_FAILED
            if isinstance(data, dict):
                msg = str(data.get("message") or data.get("status") or msg)
                code = str(data.get("code") or "")
            if status == 426 or "maximum" in (raw_body or "").lower() + msg.lower():
                return self._rate_limit_payload()
            return {
                "items": [],
                "error": True,
                "message": msg,
                "reason": "http_error",
            }

        if not isinstance(data, dict):
            return {"items": [], "error": True, "message": MSG_NEWS_FETCH_FAILED, "reason": "bad_response"}

        st = data.get("status")
        if st == "error":
            em = str(data.get("message") or "").lower()
            if "maximum" in em or "rate" in em and "limit" in em:
                return self._rate_limit_payload()
            if "api" in em and "key" in em:
                return {"items": [], "error": True, "message": MSG_NEWS_KEY_MISSING, "reason": "auth"}
            return {
                "items": [],
                "error": True,
                "message": str(data.get("message") or MSG_NEWS_FETCH_FAILED),
                "reason": "upstream",
            }

        articles = data.get("articles")
        if not isinstance(articles, list):
            return {"items": [], "error": False, "message": None, "reason": None}

        items: list[dict[str, Any]] = []
        for row in articles:
            if not isinstance(row, dict):
                continue
            src = row.get("source") if isinstance(row.get("source"), dict) else {}
            source_name = src.get("name") if isinstance(src, dict) else row.get("source")
            cand = _normalize_article(
                {
                    "title": row.get("title"),
                    "source": source_name,
                    "published_at": row.get("publishedAt"),
                    "summary": row.get("description"),
                    "url": row.get("url"),
                }
            )
            if not cand:
                continue
            items.append(cand)

        items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        return {"items": items, "error": False, "message": None, "reason": None}


def create_news_provider() -> NewsProvider:
    mode = os.getenv("RAGX_NEWS_PROVIDER", "auto").strip().lower()
    keyed: dict[str, type[_KeyedNewsProviderBase]] = {
        "finnhub": FinnhubNewsProvider,
        "alpha_vantage": AlphaVantageNewsProvider,
        "alpha": AlphaVantageNewsProvider,
        "newsapi": NewsApiOrgProvider,
    }

    if mode == "mock":
        return MockNewsProvider()

    if mode == "auto" or mode == "":
        for cls in (FinnhubNewsProvider, AlphaVantageNewsProvider, NewsApiOrgProvider):
            provider = cls.from_env()
            if provider is not None:
                logger.info("Investor news: using %s (auto)", provider.name)
                return provider
        logger.warning(
            "Investor news: no API keys — set FINNHUB_API_KEY, ALPHA_VANTAGE_API_KEY, or NEWSAPI_API_KEY (or RAGX_NEWS_PROVIDER=mock)."
        )
        return MissingNewsApiKeyNewsProvider()

    cls = keyed.get(mode)
    if cls is None:
        logger.warning("Unknown RAGX_NEWS_PROVIDER=%r. Using missing-key placeholder.", mode)
        return MissingNewsApiKeyNewsProvider()

    provider = cls.from_env()
    if provider is None:
        logger.warning("RAGX_NEWS_PROVIDER=%s but %s is not set.", mode, cls.env_key)
        return MissingNewsApiKeyNewsProvider()
    return provider
