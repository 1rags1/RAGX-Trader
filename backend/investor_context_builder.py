"""
Builds structured investor context payload for future LLM integration.
"""

from __future__ import annotations

from typing import Any


def build_investor_context(
    *,
    selected_ticker: str,
    price_trend: dict[str, Any],
    news_list: list[dict[str, Any]],
    score_breakdown: dict[str, Any],
    risk_factors: list[str],
    user_question: str | None = None,
) -> dict[str, Any]:
    return {
        "selected_ticker": selected_ticker.upper(),
        "price_trend": price_trend,
        "news_list": news_list,
        "score_breakdown": score_breakdown,
        "risk_factors": risk_factors,
        "user_question": (user_question or "").strip() or None,
    }

