"""
Investor scoring — evidence pillars for individual equities only.

Pillars: trend, momentum, news, risk, consistency (steady directional price path).
Thin history, gaps in news/charts, whip-saw regimes, or extreme swings cap and penalize the score so Top 3 is not inflated on noise.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from backend.investor_universe import is_eligible_stock_opportunity


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _pearson_r(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 3:
        return 0.0
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(len(xs)))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-14 or dy < 1e-14:
        return 0.0
    return _clamp(num / (dx * dy), -1.0, 1.0)


def _extract_prices(points: list[dict[str, Any]]) -> list[float]:
    prices: list[float] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        raw = p.get("close") if p.get("close") is not None else p.get("price")
        try:
            prices.append(float(raw))
        except (TypeError, ValueError):
            continue
    return prices


def _news_sentiment_score(news_items: list[dict[str, Any]]) -> float:
    if not news_items:
        return 0.0
    positive_terms = ("beat", "growth", "expansion", "record", "upgrade", "strong")
    caution_terms = ("miss", "downgrade", "lawsuit", "probe", "risk", "slowdown", "cuts")
    score = 0.0
    sample = news_items[:12]
    for item in sample:
        text = f"{str(item.get('headline') or '').lower()} {str(item.get('summary') or '').lower()}"
        for t in positive_terms:
            if t in text:
                score += 1.0
        for t in caution_terms:
            if t in text:
                score -= 1.0
    return _clamp(score / max(1.0, len(sample)), -2.5, 2.5)


def _consistency_score(prices: list[float]) -> float:
    """
    Measures how cleanly log-price tracks time (Pearson r × scale).
    Choppy sideways → ~0; smooth trends → higher |component|.
    """
    n = len(prices)
    if n < 10 or prices[0] <= 0:
        return 0.0
    try:
        y = [math.log(p / prices[0]) for p in prices if p > 0]
    except ValueError:
        return 0.0
    if len(y) < 10:
        return 0.0
    x = list(range(len(y)))
    r = _pearson_r([float(v) for v in x], [float(v) for v in y])
    return _clamp(r * 2.35, -2.0, 2.0)


def _time_series_metrics(
    prices: list[float],
) -> tuple[float, float, float, float, float, float]:
    """
    trend_s, momentum_s, risk_s, daily_perf, weekly_perf, avg_abs_move — same semantics as prior engine.
    """
    if len(prices) < 6 or prices[0] <= 0:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    last = prices[-1]
    first = prices[0]
    total_return = (last - first) / first if first > 0 else 0.0
    daily_ref = prices[-2] if len(prices) >= 2 else first
    weekly_ref = prices[-6] if len(prices) >= 6 else first
    daily_perf = (last - daily_ref) / daily_ref if daily_ref > 0 else 0.0
    weekly_perf = (last - weekly_ref) / weekly_ref if weekly_ref > 0 else 0.0
    n = len(prices)
    short_n = max(3, int(n * 0.2))
    long_n = max(short_n + 1, int(n * 0.6))
    short_avg = sum(prices[-short_n:]) / short_n
    long_avg = sum(prices[-long_n:]) / long_n
    trend_raw = (short_avg - long_avg) / long_avg if long_avg else 0.0
    abs_moves: list[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        cur = prices[i]
        if prev > 0:
            abs_moves.append(abs((cur - prev) / prev))
    avg_abs_move = sum(abs_moves) / len(abs_moves) if abs_moves else 0.0
    trend_component = _clamp((trend_raw * 16.0) + (total_return * 3.5), -2.0, 2.0)
    momentum_component = _clamp((weekly_perf * 10.0) + (daily_perf * 5.0), -2.0, 2.0)
    risk_component = _clamp(1.35 - avg_abs_move * 40.0, -2.0, 2.0)
    return (
        trend_component,
        momentum_component,
        risk_component,
        daily_perf,
        weekly_perf,
        avg_abs_move,
    )


def _volatility_risk_label(avg_abs_daily_move: float) -> str:
    pct = max(0.0, avg_abs_daily_move * 100.0)
    if pct >= 2.8:
        return "Elevated"
    if pct >= 1.45:
        return "Moderate"
    return "Contained"


def _profile_score(profile: dict[str, Any]) -> float:
    if not profile:
        return 0.0
    populated = sum(1 for k in ("company_name", "exchange", "asset_type") if profile.get(k))
    return _clamp(populated * 0.25, 0.0, 0.75)


def _score_ceiling_from_depth(n: int) -> int:
    """Never allow “top pick” scores on a handful of bars."""
    if n >= 52:
        return 100
    if n >= 30:
        return 74
    if n >= 20:
        return 64
    if n >= 12:
        return 54
    if n >= 6:
        return 44
    return 32


def _non_equity_fallback(symbol: str, profile: dict[str, Any] | None) -> dict[str, Any]:
    msg = (
        "This ticker is screened out of the equity opportunity stack (benchmark/ETF or non‑equity profile). "
        "Scores here are capped for reference only."
    )
    sym = symbol.upper()
    return {
        "symbol": sym,
        "score": 22,
        "rating": "Cautious",
        "explanation": "Not modeled as an individual-stock opportunity.",
        "why_ranked": msg,
        "risk_warning": "Risk warning: use ETFs/benchmarks for context rather than headline stock ranks.",
        "risk_factors": ["not in core equity recommendation universe"],
        "breakdown": {
            "trend_score": 0.0,
            "momentum_score": 0.0,
            "news_score": 0.0,
            "risk_score": 0.0,
            "consistency_score": 0.0,
            "company_profile_score": 0.0,
            "data_points_used": 0,
            "data_quality_notes": ["non-equity benchmark or blocked name"],
            "penalties_detail": [],
            "pre_penalty_score": 22,
            "score_ceiling_applied": 32,
            "avg_abs_daily_move_pct": 0.0,
            "volatility_risk_level": "Unknown",
        },
        "quote": {},
    }


def build_investor_score(
    *,
    symbol: str,
    quote: dict[str, Any] | None,
    time_series: dict[str, Any] | None,
    news_items: list[dict[str, Any]] | None,
    profile: dict[str, Any] | None,
    market_context: dict[str, Any] | None = None,
    news_feed_error: bool = False,
) -> dict[str, Any]:
    sym = symbol.upper().strip()
    profile = profile or {}
    if not is_eligible_stock_opportunity(sym, profile):
        return _non_equity_fallback(sym, profile)

    ts = time_series or {}
    ts_error = bool(ts.get("error"))
    points = ts.get("points") or []
    prices = _extract_prices(points if isinstance(points, list) else [])
    n_points = len(prices)

    news_list = [x for x in (news_items or []) if isinstance(x, dict)]
    has_news = len(news_list) > 0

    trend_s, momentum_s, risk_s, daily_perf, weekly_perf, avg_abs_move = _time_series_metrics(prices)
    consistency_s = _consistency_score(prices)
    news_s = _news_sentiment_score(news_list)
    profile_s = _profile_score(profile)

    context = market_context or {}
    benchmark_weekly = float(context.get("benchmark_weekly_performance") or 0.0)
    relative_weekly = weekly_perf - benchmark_weekly
    rel_note = relative_weekly

    # Pillar blend (evidence-weighted; profile is a tiny identity check only)
    pillar_core = (
        trend_s * 0.24
        + momentum_s * 0.22
        + news_s * 0.18
        + risk_s * 0.14
        + consistency_s * 0.22
    )
    weighted = _clamp(pillar_core + profile_s * 0.04, -2.6, 2.6)
    pre_penalty = float(_clamp(50.0 + weighted * 11.5, 0.0, 100.0))

    penalties: list[dict[str, Any]] = []
    penalty_total = 0.0

    def _pen(amount: float, code: str, note: str) -> None:
        nonlocal penalty_total
        penalty_total += amount
        penalties.append({"code": code, "points": round(amount, 2), "note": note})

    if ts_error or n_points < 6:
        _pen(
            18.0 + max(0, 6 - n_points) * 0.9,
            "thin_or_failed_chart",
            "Chart/price series missing or shorter than minimum depth for conviction scoring.",
        )
    elif n_points < 12:
        _pen(10.0, "short_series", "Price history shorter than preferred window — confidence is capped.")

    if news_feed_error:
        _pen(16.0, "news_feed_error", "Headline provider returned an error; news pillar is unreliable.")
    elif not has_news:
        _pen(11.0, "missing_news", "No recent headlines surfaced — penalized until news coverage exists.")

    avg_move_pct = avg_abs_move * 100.0
    if avg_move_pct >= 4.8:
        _pen(13.0, "extreme_realized_vol", "Day-to-day swings are unusually large for conservative ranks.")
    elif avg_move_pct >= 3.4:
        _pen(8.0, "high_realized_vol", "Elevated day-to-day noise versus calmer alternatives.")

    if abs(trend_s) < 0.30 and abs(consistency_s) < 0.42:
        _pen(9.0, "unclear_trend", "Trend and path consistency disagree — structure looks sideways or noisy.")

    ceiling = _score_ceiling_from_depth(n_points)

    interim = float(_clamp(pre_penalty - penalty_total, 0.0, float(ceiling)))

    dq_notes: list[str] = []
    if n_points < 20:
        dq_notes.append(f"Only {n_points} usable closes contributed — rank cannot exceed {ceiling}.")
    if news_feed_error:
        dq_notes.append("News pillar discounted because the feed signaled an error.")

    rating = "Cautious"
    tier_line = ""
    if interim >= 62:
        rating = "Bullish"
        tier_line = "Bullish: pillars line up enough for placement near the front of this screen."
    elif interim >= 42:
        rating = "Neutral"
        tier_line = "Neutral: evidence is workable but mixes offsets (data gaps, chop, or mixed signals)."
    else:
        tier_line = "Cautious: multiple evidence checks failed or capped the headline score."

    why_parts = [
        "Why this score (evidence pillars on this window): "
        + f"trend {trend_s:+.2f}, momentum {momentum_s:+.2f}, headlines {news_s:+.2f}, "
        + f"risk {risk_s:+.2f} (calmer tapes score higher), consistency {consistency_s:+.2f} "
        "(how cleanly prices progress through time)."
    ]
    if n_points >= 6:
        why_parts.append(
            f"Price action uses {n_points} closes; vs SPY benchmark weekly excess was "
            f"{rel_note * 100:+.2f} percentage points (context only)."
        )
    if penalties:
        why_parts.append(
            "Applied adjustments: "
            + "; ".join(f"{p['code']} (−{p['points']} pts)" for p in penalties)
            + "."
        )
    if ceiling < 100:
        why_parts.append(
            f"A data-depth cap keeps the headline score at or below {ceiling}/100 while only "
            f"{n_points} closing prices are available for this interval — not enough bars for a top rank."
        )

    why_ranked = " ".join(why_parts)

    risk_factors: list[str] = []
    if risk_s < -0.45:
        risk_factors.append("realized swings are chunky")
    if not has_news:
        risk_factors.append("thin headline corroboration")
    if avg_move_pct >= 2.85:
        risk_factors.append(f"median daily noise bucket: {_volatility_risk_label(avg_abs_move)}")
    if abs(trend_s) < 0.35:
        risk_factors.append("trend slope is tentative")
    if not risk_factors:
        risk_factors.append("conditions can deteriorate rapidly — re-check pillars often")

    return {
        "symbol": sym,
        "score": int(round(interim)),
        "rating": rating,
        "explanation": tier_line,
        "why_ranked": why_ranked,
        "risk_warning": "Risk warning: " + "; ".join(risk_factors) + ".",
        "risk_factors": risk_factors,
        "breakdown": {
            "trend_score": round(trend_s, 3),
            "momentum_score": round(momentum_s, 3),
            "news_score": round(news_s, 3),
            "risk_score": round(risk_s, 3),
            "consistency_score": round(consistency_s, 3),
            "company_profile_score": round(profile_s, 3),
            "relative_vs_spy_weekly_decimal": round(relative_weekly, 6),
            "daily_performance": round(daily_perf, 4),
            "weekly_performance": round(weekly_perf, 4),
            "benchmark_weekly_performance": round(benchmark_weekly, 4),
            "avg_abs_daily_move_pct": round(avg_move_pct, 4),
            "volatility_risk_level": _volatility_risk_label(avg_abs_move),
            "data_points_used": n_points,
            "data_quality_notes": dq_notes,
            "penalties_detail": penalties,
            "pillar_weighted_blend": round(weighted, 4),
            "pre_penalty_score": round(pre_penalty, 2),
            "penalty_total": round(penalty_total, 2),
            "score_ceiling_applied": ceiling,
            "overall_investor_score": int(round(interim)),
        },
        "quote": quote or {},
    }
