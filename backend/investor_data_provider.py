"""
Investor market data provider layer.

Design goals:
- Single interface for multiple vendors.
- API key usage via environment variables only.
- Graceful fallback to mock provider when keys are missing.
"""

from __future__ import annotations

import logging
import os
import random
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from abc import ABC, abstractmethod
from typing import Any

from backend.investor_universe import search_stock_universe

logger = logging.getLogger(__name__)


class InvestorDataProvider(ABC):
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
    def get_company_news(self, symbol: str) -> list[dict[str, Any]]:
        pass

    @abstractmethod
    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        pass


class MockInvestorDataProvider(InvestorDataProvider):
    """Default safe provider used when vendor keys are unavailable."""

    name = "mock"

    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        return list(search_stock_universe(query, limit=25))

    def get_quote(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol.upper(), "price": None, "change_percent": None, "provider": self.name}

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
        points: list[dict[str, float | int]] = []
        for i in range(points_count):
            drift = 0.0006
            shock = rnd.uniform(-vol, vol)
            price = max(5.0, price * (1.0 + drift + shock))
            ts = int((t0 + timedelta(minutes=i * step_minutes)).timestamp())
            points.append({"time": ts, "price": round(price, 2)})
        return {"symbol": sym, "interval": rng, "points": points, "provider": self.name}

    def get_company_news(self, symbol: str) -> list[dict[str, Any]]:
        return []

    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol.upper(), "company_name": None, "exchange": None, "asset_type": None, "provider": self.name}


class _KeyedProviderBase(InvestorDataProvider):
    """Shared behavior for providers that require an API key."""

    env_key: str = ""
    name: str = "keyed"

    def __init__(self, api_key: str):
        self.api_key = api_key

    @classmethod
    def from_env(cls) -> "_KeyedProviderBase | None":
        key = os.getenv(cls.env_key, "").strip()
        if not key:
            return None
        return cls(key)

    def search_symbols(self, query: str) -> list[dict[str, Any]]:
        return []

    def get_quote(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol.upper(), "provider": self.name, "unavailable": True}

    def get_time_series(self, symbol: str, interval: str) -> dict[str, Any]:
        return {"symbol": symbol.upper(), "interval": interval, "points": [], "provider": self.name, "unavailable": True}

    def get_company_news(self, symbol: str) -> list[dict[str, Any]]:
        return []

    def get_company_profile(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol.upper(), "provider": self.name, "unavailable": True}


class FinnhubProvider(_KeyedProviderBase):
    name = "finnhub"
    env_key = "FINNHUB_API_KEY"

    def get_company_news(self, symbol: str) -> list[dict[str, Any]]:
        sym = symbol.upper()
        end_dt = datetime.now(UTC).date()
        start_dt = end_dt - timedelta(days=14)
        params = urllib.parse.urlencode(
            {
                "symbol": sym,
                "from": start_dt.isoformat(),
                "to": end_dt.isoformat(),
                "token": self.api_key,
            }
        )
        url = f"https://finnhub.io/api/v1/company-news?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "RAGX-Trader/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = resp.read()
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, list):
                return []
            items: list[dict[str, Any]] = []
            for row in data:
                if not isinstance(row, dict):
                    continue
                ts = row.get("datetime")
                published = None
                if isinstance(ts, (int, float)):
                    published = datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")
                items.append(
                    {
                        "headline": row.get("headline"),
                        "source": row.get("source"),
                        "published_at": published,
                        "summary": row.get("summary"),
                        "url": row.get("url"),
                    }
                )
            return items
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            logger.exception("Finnhub news fetch failed for %s", sym)
            return []


class AlphaVantageProvider(_KeyedProviderBase):
    name = "alpha_vantage"
    env_key = "ALPHA_VANTAGE_API_KEY"

    def get_company_news(self, symbol: str) -> list[dict[str, Any]]:
        sym = symbol.upper()
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
        req = urllib.request.Request(url, headers={"User-Agent": "RAGX-Trader/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                payload = resp.read()
            data = json.loads(payload.decode("utf-8"))
            feed = data.get("feed")
            if not isinstance(feed, list):
                return []
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
                items.append(
                    {
                        "headline": row.get("title"),
                        "source": row.get("source"),
                        "published_at": published,
                        "summary": row.get("summary"),
                        "url": row.get("url"),
                    }
                )
            return items
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            logger.exception("Alpha Vantage news fetch failed for %s", sym)
            return []


class TwelveDataProvider(_KeyedProviderBase):
    name = "twelve_data"
    env_key = "TWELVE_DATA_API_KEY"


def create_investor_provider() -> InvestorDataProvider:
    """
    Provider selection:
    - RAGX_INVESTOR_PROVIDER=finnhub|alpha_vantage|twelve_data|mock
    - Falls back to mock when selected provider key is missing.
    """
    requested = os.getenv("RAGX_INVESTOR_PROVIDER", "mock").strip().lower()
    builders: dict[str, type[_KeyedProviderBase]] = {
        "finnhub": FinnhubProvider,
        "alpha_vantage": AlphaVantageProvider,
        "twelve_data": TwelveDataProvider,
    }

    if requested == "mock" or not requested:
        logger.info("Investor provider selected: mock")
        return MockInvestorDataProvider()

    provider_cls = builders.get(requested)
    if not provider_cls:
        logger.warning("Unknown RAGX_INVESTOR_PROVIDER=%r. Falling back to mock.", requested)
        return MockInvestorDataProvider()

    provider = provider_cls.from_env()
    if provider is None:
        logger.warning(
            "Investor provider '%s' selected but %s is missing. Falling back to mock.",
            requested,
            provider_cls.env_key,
        )
        return MockInvestorDataProvider()

    logger.info("Investor provider selected: %s", provider.name)
    return provider

