"""
Rule-based investor summary generator from structured context.
"""

from __future__ import annotations

from typing import Any


def _trend_label(price_trend: dict[str, Any]) -> str:
    points = (price_trend or {}).get("points") or []
    if not points or len(points) < 2:
        return "trend is unclear"
    prices = [float(p["price"]) for p in points if p.get("price") is not None]
    if len(prices) < 2 or prices[0] <= 0:
        return "trend is unclear"
    perf = (prices[-1] - prices[0]) / prices[0]
    if perf > 0.08:
        return "trend is strong and upward"
    if perf > 0.02:
        return "trend is modestly upward"
    if perf < -0.08:
        return "trend is clearly downward"
    if perf < -0.02:
        return "trend is slightly downward"
    return "trend is mostly sideways"


def _top_news(news_list: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in news_list or []:
        if not isinstance(n, dict) or not n.get("headline"):
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out


def generate_rule_based_summary(context: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any]:
    quote = quote or {}
    symbol = str(context.get("selected_ticker") or "").upper()
    price_trend = context.get("price_trend") or {}
    news_list = context.get("news_list") or []
    score_breakdown = context.get("score_breakdown") or {}
    risk_factors = context.get("risk_factors") or []
    score = score_breakdown.get("score")
    rating = score_breakdown.get("rating") or "Neutral"
    trend = _trend_label(price_trend)
    top_news = _top_news(news_list)
    top_headline = top_news[0]["headline"] if top_news else None
    price = quote.get("price")
    change_pct = quote.get("change_percent")

    what = (
        f"{symbol} currently has a {rating.lower()} long-term read with score {score}/100 and the {trend}."
        if score is not None
        else f"{symbol} currently has mixed long-term signals and the {trend}."
    )
    if price is not None:
        what += f" Latest observed price input is {price}."
    if change_pct is not None:
        what += f" Recent change input is {change_pct}%."

    why = (
        "For long-term investors, trend quality, durability of news flow, and risk profile "
        "often matter more than short-term price noise."
    )

    help_bits = []
    if "upward" in trend:
        help_bits.append("Continuation of the current upward trend could support confidence.")
    if top_headline:
        help_bits.append(f"Recent headline flow includes: \"{top_headline}\".")
    help_bits.append("Consistent execution and stable macro conditions could improve the setup.")

    hurt_bits = [
        "A trend reversal can reduce conviction.",
        "Weak earnings or negative guidance can pressure sentiment.",
    ]
    if risk_factors:
        hurt_bits.append("; ".join(str(x) for x in risk_factors) + ".")

    conclusion = (
        "Overall, this looks like a strong setup worth watching."
        if rating == "Bullish"
        else "Overall, this is a watch setup with balanced upside and risk."
        if rating == "Neutral"
        else "Overall, caution is reasonable until evidence improves."
    )

    sources = [
        {"label": "Quote feed", "type": "quote", "detail": str(quote.get("provider") or "provider layer")},
        {"label": "Price trend", "type": "time_series", "detail": str(price_trend.get("interval") or "range")},
        {"label": "Investor score", "type": "scoring", "detail": "Rule-based investor score engine"},
    ]
    for n in top_news:
        sources.append(
            {
                "label": str(n.get("source") or "News source"),
                "type": "news",
                "detail": str(n.get("headline") or "Headline"),
                "url": str(n.get("url") or ""),
            }
        )

    return {
        "symbol": symbol,
        "sections": {
            "what_is_happening": what,
            "why_it_matters": why,
            "what_could_help": help_bits,
            "what_could_hurt": hurt_bits,
            "overall_conclusion": conclusion,
        },
        "sources": sources,
    }

