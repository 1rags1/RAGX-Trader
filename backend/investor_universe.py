"""
Investor stock universe service (mock data for now).

This module is intentionally provider-agnostic so a real market data/search API
can be swapped in later without changing route contracts.
"""

from __future__ import annotations

from typing import TypedDict


class StockUniverseItem(TypedDict):
    ticker: str
    company_name: str
    exchange: str
    asset_type: str


MOCK_STOCK_UNIVERSE: list[StockUniverseItem] = [
    {"ticker": "AAPL", "company_name": "Apple Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "MSFT", "company_name": "Microsoft Corporation", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "NVDA", "company_name": "NVIDIA Corporation", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "GOOGL", "company_name": "Alphabet Inc. Class A", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "AMZN", "company_name": "Amazon.com, Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "META", "company_name": "Meta Platforms, Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "TSLA", "company_name": "Tesla, Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "SPY", "company_name": "SPDR S&P 500 ETF Trust", "exchange": "NYSE Arca", "asset_type": "ETF"},
    {"ticker": "QQQ", "company_name": "Invesco QQQ Trust", "exchange": "NASDAQ", "asset_type": "ETF"},
    {"ticker": "BRK.B", "company_name": "Berkshire Hathaway Inc. Class B", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "JPM", "company_name": "JPMorgan Chase & Co.", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "V", "company_name": "Visa Inc.", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "MA", "company_name": "Mastercard Incorporated", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "UNH", "company_name": "UnitedHealth Group Incorporated", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "XOM", "company_name": "Exxon Mobil Corporation", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "JNJ", "company_name": "Johnson & Johnson", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "WMT", "company_name": "Walmart Inc.", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "PG", "company_name": "Procter & Gamble Company", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "KO", "company_name": "Coca-Cola Company", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "PEP", "company_name": "PepsiCo, Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "AVGO", "company_name": "Broadcom Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "AMD", "company_name": "Advanced Micro Devices, Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "INTC", "company_name": "Intel Corporation", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "NFLX", "company_name": "Netflix, Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "DIS", "company_name": "The Walt Disney Company", "exchange": "NYSE", "asset_type": "Equity"},
    {"ticker": "PLTR", "company_name": "Palantir Technologies Inc.", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "COST", "company_name": "Costco Wholesale Corporation", "exchange": "NASDAQ", "asset_type": "Equity"},
    {"ticker": "LLY", "company_name": "Eli Lilly and Company", "exchange": "NYSE", "asset_type": "Equity"},
]

# Benchmarks / index proxies for Market Overview & scoring context only — not for Top 3 stock picks.
MARKET_OVERVIEW_BENCHMARK_TICKERS: tuple[str, ...] = ("SPY", "QQQ")

# Default universe scanned for Top 3 stock opportunities (individual names only).
DEFAULT_STOCK_OPPORTUNITY_UNIVERSE: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "TSLA",
    "AMD",
    "NFLX",
    "AVGO",
    "COST",
    "JPM",
    "BRK.B",
    "LLY",
    "XOM",
]

# Broad blocklist: common index / ETF products; never surface these as stock recommendations.
_INDEX_ETF_TICKER_DENYLIST: frozenset[str] = frozenset(
    {
        "SPY",
        "QQQ",
        "DIA",
        "VOO",
        "IVV",
        "QQQM",
        "SPLG",
        "SCHX",
        "VTI",
        "IWM",
        "IJH",
        "MDY",
    }
)


def _is_non_equity_asset_type(asset_type: str) -> bool:
    at = (asset_type or "").strip().upper()
    if not at:
        return False
    if at in {"ETF", "ETN", "FUND", "MUTUAL FUND", "INDEX FUND", "TRUST FUND"}:
        return True
    if "ETF" in at or " ETN" in at or "INDEX FUND" in at or "MUTUAL FUND" in at:
        return True
    return False


def is_eligible_stock_opportunity(symbol: str, profile: dict | None) -> bool:
    """
    Top recommendations are for individual operating companies only (not ETFs/index funds).
    Uses ticker denylist plus profile asset_type / name heuristics when available.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return False
    if sym in _INDEX_ETF_TICKER_DENYLIST:
        return False
    p = profile or {}
    at = str(p.get("asset_type") or "").strip()
    if _is_non_equity_asset_type(at):
        return False
    nm = str(p.get("company_name") or "").upper()
    if " ETF" in nm or nm.endswith(" ETF") or " ETN" in nm or "INDEX FUND" in nm:
        return False
    if "S&P 500" in nm and ("ETF" in nm or "TRUST" in nm):
        return False
    if "NASDAQ" in nm and "100" in nm and ("ETF" in nm or "TRUST" in nm):
        return False
    return True


def search_stock_universe(query: str, limit: int = 20) -> list[StockUniverseItem]:
    q = (query or "").strip().upper()
    if not q:
        return []
    out: list[StockUniverseItem] = []
    for item in MOCK_STOCK_UNIVERSE:
        ticker = item["ticker"].upper()
        company = item["company_name"].upper()
        if q in ticker or q in company:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def get_universe_item(symbol: str) -> StockUniverseItem | None:
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    for item in MOCK_STOCK_UNIVERSE:
        if item["ticker"].upper() == sym:
            return item
    return None

