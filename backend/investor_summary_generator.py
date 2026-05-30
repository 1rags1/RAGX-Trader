"""
Rule-based investor summary generator from structured context.
6–12 month research framing — not short-term trading signals.
"""

from __future__ import annotations

from typing import Any

from backend.investor_score_engine import OUTLOOK_LABEL, RESEARCH_DISCLAIMER


def _trend_label(price_trend: dict[str, Any]) -> str:
    points = (price_trend or {}).get("points") or []
    if not points or len(points) < 2:
        return "long-term trend is unclear on this window"
    prices: list[float] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            prices.append(float(raw))
        except (TypeError, ValueError):
            continue
    if len(prices) < 2 or prices[0] <= 0:
        return "long-term trend is unclear on this window"
    perf = (prices[-1] - prices[0]) / prices[0]
    if perf > 0.14:
        return "multi-month price path supports a constructive 6–12 month setup"
    if perf > 0.04:
        return "long-term trend is modestly positive"
    if perf < -0.14:
        return "multi-month price path weakens the 6–12 month thesis"
    if perf < -0.04:
        return "long-term trend is slightly negative"
    return "long-term trend is mostly sideways — narrative matters more"


def _top_news(news_list: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in news_list or []:
        if not isinstance(n, dict) or not n.get("headline"):
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out


def _watchlist_note(rating: str) -> str:
    r = (rating or "").strip().lower()
    if "strong" in r and "watch" in r:
        return "Strong watchlist candidate for a 6–12 month research list."
    if r == "watch" or (r.startswith("watch") and "strong" not in r):
        return "Worth tracking on a watchlist over a 6–12 month horizon."
    if "neutral" in r:
        return "Monitor on a watchlist — evidence is mixed for now."
    if "cautious" in r:
        return "Lower-priority watchlist name until the long-term setup improves."
    if "avoid" in r or "high risk" in r:
        return "Not a priority watchlist name on current evidence."
    return "Use as research context only — not a trading signal."


def _concise_why_ranked(score_breakdown: dict[str, Any], trend: str, symbol: str) -> str:
    why = str(score_breakdown.get("why_ranked") or "").strip()
    if why:
        return why
    explanation = str(score_breakdown.get("explanation") or "").strip()
    if explanation:
        return explanation
    score = score_breakdown.get("score")
    if score is not None:
        return (
            f"{symbol} scores {score}/100 on long-term trend, company quality, news sentiment, "
            f"risk, consistency, and data confidence — {trend}."
        )
    return f"Evidence is mixed on {symbol}; {trend}."


def _strengths_from_breakdown(bd: dict[str, Any], trend: str, top_headline: str | None) -> list[str]:
    bits: list[str] = []
    if float(bd.get("long_term_trend_pts") or 0) >= 16:
        bits.append("Multi-month price direction supports the 6–12 month outlook.")
    elif "positive" in trend or "constructive" in trend:
        bits.append("Long-term price path is constructive relative to a flat market.")
    if float(bd.get("company_quality_pts") or 0) >= 14:
        bits.append("Business profile and scale support investor-confidence screening.")
    if float(bd.get("news_narrative_pts") or 0) >= 12:
        bits.append("Headline flow supports a believable growth narrative.")
    elif top_headline:
        bits.append(f'Recent headline context: "{top_headline[:100]}{"…" if len(top_headline) > 100 else ""}".')
    if float(bd.get("risk_control_pts") or 0) >= 12:
        bits.append("Volatility looks manageable for a risk-adjusted 6–12 month hold.")
    if float(bd.get("momentum_consistency_pts") or 0) >= 7:
        bits.append("Price path shows reasonable consistency over the selected window.")
    if not bits:
        bits.append("Stable execution and improving narrative could strengthen the long-term setup.")
    return bits[:3]


def _conclusion_for_rating(rating: str) -> str:
    r = (rating or "").strip()
    rl = r.lower()
    if "strong" in rl and "watch" in rl:
        return (
            "Overall, business quality, trend, and narrative align for a strong 6–12 month watchlist candidate."
        )
    if rl == "watch" or (rl.startswith("watch") and "strong" not in rl):
        return "Overall, this is a balanced long-term setup worth tracking on a watchlist."
    if "neutral" in rl:
        return "Overall, the 6–12 month case is mixed — keep researching before commitment."
    if "cautious" in rl:
        return "Overall, several pillars weaken the long-term thesis — proceed with extra diligence."
    if "avoid" in rl or "high risk" in rl:
        return "Overall, weak long-term evidence or data gaps — lower priority on a research watchlist."
    return "Overall, treat this as research support for a 6–12 month outlook, not a trading call."


def _short_executive_summary(
    symbol: str,
    rating: str,
    trend: str,
    score: Any,
    conclusion: str,
    top_headline: str | None,
    watchlist: str,
) -> str:
    parts: list[str] = []
    if score is not None:
        parts.append(
            f"{symbol} earns a {rating} read at {score}/100 for a {OUTLOOK_LABEL.lower()} — {trend}."
        )
    else:
        parts.append(f"{symbol} shows a {rating} long-term setup — {trend}.")
    parts.append(conclusion.rstrip(".") + ".")
    parts.append(watchlist.rstrip(".") + ".")
    if top_headline:
        parts.append(
            f'Supporting headline: "{top_headline[:110]}{"…" if len(top_headline) > 110 else ""}".'
        )
    parts.append(RESEARCH_DISCLAIMER)
    text = " ".join(parts)
    if len(text) > 620:
        return text[:617].rsplit(" ", 1)[0] + "…"
    return text


def generate_rule_based_summary(context: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any]:
    quote = quote or {}
    symbol = str(context.get("selected_ticker") or "").upper()
    price_trend = context.get("price_trend") or {}
    news_list = context.get("news_list") or []
    score_breakdown = context.get("score_breakdown") or {}
    risk_factors = context.get("risk_factors") or []
    score = score_breakdown.get("score")
    rating = score_breakdown.get("rating") or "Neutral"
    bd = score_breakdown.get("breakdown") or {}
    trend = _trend_label(price_trend)
    top_news = _top_news(news_list)
    top_headline = top_news[0]["headline"] if top_news else None
    watchlist = _watchlist_note(rating)
    price = quote.get("price")
    change_pct = quote.get("change_percent")

    what = (
        f"{symbol} currently has a {rating} read for a {OUTLOOK_LABEL.lower()} "
        f"with score {score}/100 — {trend}."
        if score is not None
        else f"{symbol} currently has mixed long-term signals for a {OUTLOOK_LABEL.lower()} — {trend}."
    )
    if price is not None:
        what += f" Latest observed price input is {price}."
    if change_pct is not None:
        what += f" Recent daily change input is {change_pct}% (not used as a short-term trade signal)."

    why = (
        "For a 6–12 month investor, business quality, durable trend, growth narrative, "
        "and risk-adjusted opportunity matter more than intraday noise or one-day moves."
    )

    help_bits = _strengths_from_breakdown(bd, trend, top_headline)
    help_bits.append("Consistent execution and stable macro conditions could improve investor confidence.")

    hurt_bits = [
        "A sustained trend reversal can weaken the 6–12 month thesis.",
        "Weak earnings, guidance cuts, or negative headlines can pressure the growth narrative.",
    ]
    if risk_factors:
        for rf in risk_factors[:3]:
            hurt_bits.append(str(rf).strip().rstrip(".") + ".")

    conclusion = _conclusion_for_rating(rating)
    why_ranked = _concise_why_ranked(score_breakdown, trend, symbol)
    short_summary = _short_executive_summary(
        symbol, rating, trend, score, conclusion, top_headline, watchlist
    )

    sources = [
        {"label": "Quote feed", "type": "quote", "detail": str(quote.get("provider") or "provider layer")},
        {"label": "Price trend", "type": "time_series", "detail": str(price_trend.get("interval") or "range")},
        {"label": "Investor score", "type": "scoring", "detail": "6–12 month investor score engine"},
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
        "research_disclaimer": RESEARCH_DISCLAIMER,
        "outlook_horizon": OUTLOOK_LABEL,
        "thesis": {
            "overall_rating": rating,
            "overall_rating_display": rating,
            "why_ranked": why_ranked,
            "key_strengths": help_bits[:3],
            "key_risks": hurt_bits[:3],
            "short_summary": short_summary,
            "watchlist_note": watchlist,
        },
        "sections": {
            "what_is_happening": what,
            "why_it_matters": why,
            "what_could_help": help_bits,
            "what_could_hurt": hurt_bits,
            "overall_conclusion": conclusion,
        },
        "sources": sources,
    }
