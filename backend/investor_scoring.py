"""
Investor scoring engine for longer-term stock analysis.

The engine combines evidence signals and outputs:
- score: 0..100
- rating: Bullish / Neutral / Cautious
- explanation: concise rationale
- risk_warning: concise cautionary note
"""

from __future__ import annotations

from typing import Any


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _news_sentiment_score(news_items: list[dict[str, Any]]) -> float:
    if not news_items:
        return 0.0
    positive_terms = ("beat", "growth", "expansion", "record", "upgrade", "strong")
    caution_terms = ("miss", "downgrade", "lawsuit", "probe", "risk", "slowdown", "cuts")
    score = 0.0
    sample = news_items[:12]
    for item in sample:
        headline = str(item.get("headline") or "").lower()
        summary = str(item.get("summary") or "").lower()
        text = f"{headline} {summary}"
        for t in positive_terms:
            if t in text:
                score += 1.0
        for t in caution_terms:
            if t in text:
                score -= 1.0
    return _clamp(score / max(1.0, len(sample)), -2.5, 2.5)


def _time_series_metrics(points: list[dict[str, Any]]) -> tuple[float, float, float]:
    """
    Returns:
    - trend_score: -2..2
    - perf_score: -2..2
    - vol_score: -2..2 (higher is lower volatility / lower risk)
    """
    if not points or len(points) < 6:
        return (0.0, 0.0, 0.0)
    prices = [float(p["price"]) for p in points if p.get("price") is not None]
    if len(prices) < 6:
        return (0.0, 0.0, 0.0)
    first = prices[0]
    last = prices[-1]
    if first <= 0:
        return (0.0, 0.0, 0.0)
    total_return = (last - first) / first

    # Simple trend proxy: compare short vs long average.
    n = len(prices)
    short_n = max(3, int(n * 0.2))
    long_n = max(short_n + 1, int(n * 0.6))
    short_avg = sum(prices[-short_n:]) / short_n
    long_avg = sum(prices[-long_n:]) / long_n
    trend_raw = (short_avg - long_avg) / long_avg if long_avg else 0.0

    # Volatility proxy: average absolute bar-to-bar return.
    abs_moves: list[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        cur = prices[i]
        if prev > 0:
            abs_moves.append(abs((cur - prev) / prev))
    avg_abs_move = sum(abs_moves) / len(abs_moves) if abs_moves else 0.0

    trend_score = _clamp(trend_raw * 20.0, -2.0, 2.0)
    perf_score = _clamp(total_return * 10.0, -2.0, 2.0)
    vol_score = _clamp(1.4 - avg_abs_move * 40.0, -2.0, 2.0)
    return (trend_score, perf_score, vol_score)


def _profile_placeholder_score(profile: dict[str, Any]) -> float:
    """
    Placeholder fundamental confidence:
    + small boost when profile fields are populated
    """
    if not profile:
        return 0.0
    populated = 0
    for key in ("company_name", "exchange", "asset_type"):
        if profile.get(key):
            populated += 1
    return _clamp(populated * 0.25, 0.0, 0.75)


def score_stock(
    *,
    symbol: str,
    quote: dict[str, Any] | None,
    time_series: dict[str, Any] | None,
    news_items: list[dict[str, Any]] | None,
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    points = (time_series or {}).get("points") or []
    trend_s, perf_s, vol_s = _time_series_metrics(points)
    news_s = _news_sentiment_score(news_items or [])
    profile_s = _profile_placeholder_score(profile or {})

    weighted = (
        trend_s * 0.30
        + perf_s * 0.24
        + news_s * 0.20
        + vol_s * 0.16
        + profile_s * 0.10
    )
    score = int(round(_clamp(50.0 + weighted * 12.0, 0.0, 100.0)))

    if score >= 68:
        rating = "Bullish"
        explanation = "The stock shows a relatively strong setup with supportive trend and momentum evidence."
    elif score >= 45:
        rating = "Neutral"
        explanation = "Signals are mixed; this looks like a watch setup while waiting for clearer confirmation."
    else:
        rating = "Cautious"
        explanation = "The current evidence leans weaker; caution is reasonable until risk/reward improves."

    risk_bits: list[str] = []
    if vol_s < -0.5:
        risk_bits.append("price swings are elevated")
    if news_s < -0.5:
        risk_bits.append("recent news flow is mixed-to-weak")
    if perf_s < -0.5:
        risk_bits.append("recent performance has been soft")
    if not risk_bits:
        risk_bits.append("market conditions can change quickly")
    risk_warning = "Risk warning: " + "; ".join(risk_bits) + "."

    return {
        "symbol": symbol.upper(),
        "score": score,
        "rating": rating,
        "explanation": explanation,
        "risk_warning": risk_warning,
        "components": {
            "price_trend": round(trend_s, 3),
            "recent_performance": round(perf_s, 3),
            "news_sentiment": round(news_s, 3),
            "volatility_risk": round(vol_s, 3),
            "company_profile": round(profile_s, 3),
        },
        "quote": quote or {},
    }

