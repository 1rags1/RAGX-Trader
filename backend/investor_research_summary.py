"""
Rule-based investor research summary builder (no external LLM).
"""

from __future__ import annotations

from typing import Any


def _trend_label(points: list[dict[str, Any]]) -> str:
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


def _top_news(news_items: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    usable: list[dict[str, Any]] = []
    for n in news_items:
        if not isinstance(n, dict):
            continue
        if not n.get("headline"):
            continue
        usable.append(n)
        if len(usable) >= limit:
            break
    return usable


def build_research_summary(
    *,
    symbol: str,
    quote: dict[str, Any] | None,
    time_series: dict[str, Any] | None,
    news_items: list[dict[str, Any]] | None,
    score_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    quote = quote or {}
    points = (time_series or {}).get("points") or []
    score_payload = score_payload or {}
    news_items = news_items or []

    score = score_payload.get("score")
    rating = score_payload.get("rating") or "Neutral"
    risk_warning = score_payload.get("risk_warning") or "Risk warning: market conditions can change quickly."
    trend = _trend_label(points)
    price = quote.get("price")
    change_pct = quote.get("change_percent")

    top_news = _top_news(news_items, limit=3)
    top_headline = top_news[0]["headline"] if top_news else None

    happening = (
        f"{symbol.upper()} currently has a {rating.lower()} long-term read with a score of {score}/100; "
        f"the {trend}."
        if score is not None
        else f"{symbol.upper()} currently has mixed long-term signals; the {trend}."
    )
    if price is not None:
        happening += f" Latest observed price input is {price}."
    if change_pct is not None:
        happening += f" Recent change input is {change_pct}%."

    why_matters = (
        "Long-term investors often benefit from aligning with durable trend and manageable risk, "
        "while monitoring whether recent news supports or challenges the core thesis."
    )

    could_help = []
    if "upward" in trend:
        could_help.append("Continuation of the current upward trend could support confidence.")
    if top_headline:
        could_help.append(f"Recent headline flow includes: \"{top_headline}\".")
    could_help.append("Improving execution and stable macro conditions could help the setup strengthen.")

    could_hurt = [
        "A trend reversal or extended sideways action can reduce conviction.",
        "Negative earnings, guidance cuts, or regulatory pressure can weaken sentiment.",
        risk_warning.replace("Risk warning: ", "").rstrip(".") + ".",
    ]

    conclusion = (
        "Overall, this looks like a strong setup worth watching."
        if rating == "Bullish"
        else "Overall, this is a watch setup with balanced upside and risk."
        if rating == "Neutral"
        else "Overall, caution is reasonable until evidence improves."
    )

    sources: list[dict[str, str]] = []
    sources.append({"label": "Quote feed", "type": "quote", "detail": str(quote.get("provider") or "provider layer")})
    sources.append({"label": "Price trend", "type": "time_series", "detail": str((time_series or {}).get("interval") or "range")})
    sources.append({"label": "Investor score", "type": "scoring", "detail": "Rule-based investor scoring engine"})
    for item in top_news:
        label = str(item.get("source") or "News source")
        url = str(item.get("url") or "")
        headline = str(item.get("headline") or "Headline")
        sources.append({"label": label, "type": "news", "detail": headline, "url": url})

    return {
        "symbol": symbol.upper(),
        "sections": {
            "what_is_happening": happening,
            "why_it_matters": why_matters,
            "what_could_help": could_help,
            "what_could_hurt": could_hurt,
            "overall_conclusion": conclusion,
        },
        "sources": sources,
    }

