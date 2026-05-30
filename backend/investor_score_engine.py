"""
Investor scoring — long-term watchlist research (not short-term trading).

Additive model (100 pts):
  long-term trend · company quality · news narrative · risk control ·
  momentum consistency · data confidence
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from backend.investor_universe import is_eligible_stock_opportunity

RESEARCH_DISCLAIMER = "This is research support, not financial advice."

OUTLOOK_LABEL = "Long-Term Outlook"

SCORE_PILLAR_DEFS: tuple[tuple[str, str, float], ...] = (
    ("long_term_trend_pts", "Long-term trend", 25.0),
    ("company_quality_pts", "Company quality", 20.0),
    ("news_narrative_pts", "News sentiment", 20.0),
    ("risk_control_pts", "Risk", 15.0),
    ("momentum_consistency_pts", "Consistency", 10.0),
    ("data_confidence_pts", "Data confidence", 10.0),
)


def _score_pillars_payload(
    trend_pts: float,
    quality_pts: float,
    narrative_pts: float,
    risk_pts: float,
    consistency_pts: float,
    data_pts: float,
) -> list[dict[str, Any]]:
    values = (trend_pts, quality_pts, narrative_pts, risk_pts, consistency_pts, data_pts)
    out: list[dict[str, Any]] = []
    for (key, label, max_pts), pts in zip(SCORE_PILLAR_DEFS, values, strict=True):
        out.append(
            {
                "key": key,
                "label": label,
                "points": round(pts, 1),
                "max_points": max_pts,
            }
        )
    return out


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


def _total_return(prices: list[float]) -> float:
    if len(prices) < 2 or prices[0] <= 0:
        return 0.0
    return (prices[-1] - prices[0]) / prices[0]


def _avg_abs_daily_move(prices: list[float]) -> float:
    moves: list[float] = []
    for i in range(1, len(prices)):
        prev = prices[i - 1]
        if prev > 0:
            moves.append(abs((prices[i] - prev) / prev))
    return sum(moves) / len(moves) if moves else 0.0


def _news_narrative_score(news_items: list[dict[str, Any]]) -> tuple[float, list[str]]:
    """Growth / quality narrative from headlines (0..1 scale)."""
    if not news_items:
        return 0.35, ["limited headline coverage for the growth narrative"]
    growth_terms = (
        "beat",
        "growth",
        "expansion",
        "record",
        "upgrade",
        "strong",
        "innovation",
        "leadership",
        "profit",
        "revenue",
        "guidance raise",
        "market share",
    )
    caution_terms = (
        "miss",
        "downgrade",
        "lawsuit",
        "probe",
        "slowdown",
        "cuts",
        "layoff",
        "recall",
        "investigation",
        "warning",
    )
    raw = 0.0
    notes: list[str] = []
    sample = news_items[:14]
    for item in sample:
        text = f"{str(item.get('headline') or '').lower()} {str(item.get('summary') or '').lower()}"
        for t in growth_terms:
            if t in text:
                raw += 0.18
        for t in caution_terms:
            if t in text:
                raw -= 0.16
    if raw > 0.4:
        notes.append("headlines skew toward growth and business momentum")
    elif raw < -0.2:
        notes.append("headlines include cautionary themes")
    else:
        notes.append("headline tone is mixed — narrative needs confirmation")
    return _clamp(0.45 + raw * 0.35, 0.0, 1.0), notes


def _consistency_0_1(prices: list[float]) -> float:
    n = len(prices)
    if n < 8 or prices[0] <= 0:
        return 0.35
    try:
        y = [math.log(p / prices[0]) for p in prices if p > 0]
    except ValueError:
        return 0.35
    if len(y) < 8:
        return 0.35
    x = list(range(len(y)))
    r = _pearson_r([float(v) for v in x], [float(v) for v in y])
    return _clamp((r + 1.0) / 2.0, 0.0, 1.0)


def _long_term_trend_pts(total_return: float, rel_vs_benchmark: float) -> tuple[float, str]:
    """25 pts — multi-month price direction; pullbacks tolerated if trend intact."""
    base = 8.0
    if total_return >= 0.28:
        base = 22.0
    elif total_return >= 0.14:
        base = 19.0
    elif total_return >= 0.05:
        base = 16.0
    elif total_return >= 0.0:
        base = 13.0
    elif total_return >= -0.08:
        base = 9.0
    elif total_return >= -0.18:
        base = 5.0
    else:
        base = 2.0
    rel_bonus = _clamp(rel_vs_benchmark * 12.0, -3.0, 5.0)
    pts = _clamp(base + rel_bonus, 0.0, 25.0)
    pct = total_return * 100.0
    note = (
        f"Long-term price path is up {pct:.1f}% over the selected window"
        if pct >= 0
        else f"Long-term price path is down {abs(pct):.1f}% over the selected window"
    )
    if rel_vs_benchmark > 0.03:
        note += "; relative strength vs SPY on the same window."
    elif rel_vs_benchmark < -0.03:
        note += "; lagging SPY on the same window."
    return pts, note


def _company_quality_pts(profile: dict[str, Any], fundamentals: dict[str, Any] | None) -> tuple[float, str]:
    """20 pts — business profile, scale, and leadership proxies."""
    fund = fundamentals or {}
    pts = 0.0
    populated = sum(1 for k in ("company_name", "exchange", "asset_type") if profile.get(k))
    pts += min(8.0, populated * 2.75)
    mc = fund.get("market_cap_usd")
    try:
        mc_f = float(mc) if mc is not None else None
    except (TypeError, ValueError):
        mc_f = None
    if mc_f and mc_f >= 200e9:
        pts += 7.0
    elif mc_f and mc_f >= 50e9:
        pts += 5.5
    elif mc_f and mc_f >= 10e9:
        pts += 4.0
    elif mc_f and mc_f >= 2e9:
        pts += 2.5
    sector = str(fund.get("sector") or profile.get("finnhubIndustry") or "").strip()
    if sector:
        pts += 2.0
    if fund.get("week52_high") is not None and fund.get("week52_low") is not None:
        pts += 2.0
    pts = _clamp(pts, 0.0, 20.0)
    if mc_f and mc_f >= 50e9:
        note = "large-cap profile supports business-quality screening"
    elif populated >= 2:
        note = "company profile is populated for long-term research"
    else:
        note = "limited company metadata — quality score uses partial inputs"
    return pts, note


def _risk_control_pts(
    prices: list[float],
    fundamentals: dict[str, Any] | None,
) -> tuple[float, str]:
    """15 pts — risk-adjusted opportunity; does not punish normal equity volatility."""
    fund = fundamentals or {}
    pts = 11.0
    avg_move = _avg_abs_daily_move(prices) * 100.0
    ann_vol = fund.get("annual_volatility_pct")
    try:
        ann_vol_f = float(ann_vol) if ann_vol is not None else None
    except (TypeError, ValueError):
        ann_vol_f = None
    if ann_vol_f is not None:
        if ann_vol_f >= 55:
            pts -= 5.0
        elif ann_vol_f >= 38:
            pts -= 2.5
        elif ann_vol_f <= 22:
            pts += 2.0
    elif avg_move >= 4.5:
        pts -= 4.0
    elif avg_move >= 3.2:
        pts -= 2.0
    elif avg_move <= 1.6 and len(prices) >= 8:
        pts += 2.0
    pts = _clamp(pts, 0.0, 15.0)
    if pts >= 12:
        note = "volatility is manageable for a long-term hold horizon"
    elif pts >= 8:
        note = "moderate volatility — size positions with a long-term risk budget"
    else:
        note = "elevated volatility — higher drawdown risk over the outlook window"
    return pts, note


def _data_confidence_pts(
    n_points: int,
    ts_error: bool,
    news_feed_error: bool,
    has_news: bool,
) -> tuple[float, list[str]]:
    """10 pts — feed completeness, not trading signal quality."""
    notes: list[str] = []
    if ts_error or n_points < 4:
        pts = 2.0
        notes.append("price history is thin or unavailable")
    elif n_points < 10:
        pts = 5.0
        notes.append("limited closing-price depth — confidence is provisional")
    elif n_points < 20:
        pts = 7.5
    else:
        pts = 9.0
    if news_feed_error:
        pts -= 2.0
        notes.append("news feed error — narrative pillar discounted")
    elif not has_news:
        pts -= 1.0
        notes.append("no recent headlines — narrative inferred from price/profile only")
    return _clamp(pts, 0.0, 10.0), notes


def _rating_from_score(score: int) -> str:
    if score >= 80:
        return "Strong Watch"
    if score >= 65:
        return "Watch"
    if score >= 50:
        return "Neutral"
    if score >= 35:
        return "Cautious"
    return "High Risk"


def _rating_explanation(rating: str, score: int) -> str:
    if rating == "Strong Watch":
        return (
            f"Strong Watch ({score}/100): business quality, long-term trend, and news sentiment align for a "
            f"{OUTLOOK_LABEL} watchlist candidate."
        )
    if rating == "Watch":
        return (
            f"Watch ({score}/100): balanced long-term research evidence — worth tracking over {OUTLOOK_LABEL}."
        )
    if rating == "Neutral":
        return (
            f"Neutral ({score}/100): mixed long-term signals — monitor before elevating on a research watchlist."
        )
    if rating == "Cautious":
        return (
            f"Cautious ({score}/100): several pillars weaken the {OUTLOOK_LABEL} thesis — research further first."
        )
    return (
        f"High Risk ({score}/100): weak long-term evidence or data gaps — lower priority for a research watchlist."
    )


def _volatility_risk_label(avg_abs_daily_move: float) -> str:
    pct = max(0.0, avg_abs_daily_move * 100.0)
    if pct >= 3.5:
        return "Elevated"
    if pct >= 2.0:
        return "Moderate"
    return "Contained"


def _non_equity_fallback(symbol: str, profile: dict[str, Any] | None) -> dict[str, Any]:
    sym = symbol.upper()
    return {
        "symbol": sym,
        "score": 28,
        "rating": "Cautious",
        "explanation": "Not modeled as an individual-stock long-term watchlist candidate.",
        "why_ranked": (
            f"{sym} is screened out of the equity watchlist universe (benchmark/ETF or non-equity profile). "
            f"Use for context only — {OUTLOOK_LABEL}."
        ),
        "risk_warning": f"Risk note: not an equity watchlist candidate. {RESEARCH_DISCLAIMER}",
        "risk_factors": ["outside core equity watchlist universe"],
        "research_disclaimer": RESEARCH_DISCLAIMER,
        "outlook_horizon": OUTLOOK_LABEL,
        "score_pillars": _score_pillars_payload(0.0, 2.0, 0.0, 5.0, 3.0, 2.0),
        "breakdown": {
            "long_term_trend_pts": 0.0,
            "company_quality_pts": 2.0,
            "news_narrative_pts": 0.0,
            "risk_control_pts": 5.0,
            "momentum_consistency_pts": 3.0,
            "data_confidence_pts": 2.0,
            "overall_investor_score": 28,
            "data_points_used": 0,
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
    fundamentals: dict[str, Any] | None = None,
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

    context = market_context or {}
    benchmark_total = float(context.get("benchmark_total_return") or 0.0)
    total_return = _total_return(prices)
    rel_vs_bench = total_return - benchmark_total

    trend_pts, trend_note = _long_term_trend_pts(total_return, rel_vs_bench)
    quality_pts, quality_note = _company_quality_pts(profile, fundamentals)
    narrative_raw, narrative_notes = _news_narrative_score(news_list)
    if news_feed_error:
        narrative_pts = _clamp(narrative_raw * 12.0, 0.0, 12.0)
    else:
        narrative_pts = _clamp(narrative_raw * 20.0, 0.0, 20.0)
    risk_pts, risk_note = _risk_control_pts(prices, fundamentals)
    consistency_pts = _clamp(_consistency_0_1(prices) * 10.0, 0.0, 10.0)
    data_pts, data_notes = _data_confidence_pts(n_points, ts_error, news_feed_error, has_news)

    total = int(round(_clamp(
        trend_pts + quality_pts + narrative_pts + risk_pts + consistency_pts + data_pts,
        0.0,
        100.0,
    )))

    rating = _rating_from_score(total)
    explanation = _rating_explanation(rating, total)

    why_parts = [
        f"{OUTLOOK_LABEL}: {trend_note}",
        quality_note + ".",
        "News narrative: " + (narrative_notes[0] if narrative_notes else "neutral") + ".",
    ]
    if data_notes:
        why_parts.append("Data confidence: " + "; ".join(data_notes[:2]) + ".")
    why_ranked = " ".join(why_parts)

    risk_factors: list[str] = []
    if total_return < -0.05:
        risk_factors.append("multi-month price trend is negative")
    if narrative_raw < 0.4:
        risk_factors.append("headline narrative is weak or cautious")
    if risk_pts < 8:
        risk_factors.append("volatility may stress a long-term hold")
    if data_pts < 5:
        risk_factors.append("research inputs are incomplete")
    if not risk_factors:
        risk_factors.append("macro, earnings, and sentiment can shift the long-term setup")

    avg_move = _avg_abs_daily_move(prices)
    pillars = _score_pillars_payload(
        trend_pts, quality_pts, narrative_pts, risk_pts, consistency_pts, data_pts
    )

    return {
        "symbol": sym,
        "score": total,
        "rating": rating,
        "explanation": explanation,
        "why_ranked": why_ranked,
        "risk_warning": "Risk factors: " + "; ".join(risk_factors) + f". {RESEARCH_DISCLAIMER}",
        "risk_factors": risk_factors,
        "research_disclaimer": RESEARCH_DISCLAIMER,
        "outlook_horizon": OUTLOOK_LABEL,
        "score_pillars": pillars,
        "quote": quote or {},
        "breakdown": {
            "long_term_trend_pts": round(trend_pts, 1),
            "company_quality_pts": round(quality_pts, 1),
            "news_narrative_pts": round(narrative_pts, 1),
            "risk_control_pts": round(risk_pts, 1),
            "momentum_consistency_pts": round(consistency_pts, 1),
            "data_confidence_pts": round(data_pts, 1),
            "overall_investor_score": total,
            "period_total_return_pct": round(total_return * 100.0, 2),
            "relative_vs_spy_return_pct": round(rel_vs_bench * 100.0, 2),
            "data_points_used": n_points,
            "avg_abs_daily_move_pct": round(avg_move * 100.0, 3),
            "volatility_risk_level": _volatility_risk_label(avg_move),
            "narrative_notes": narrative_notes,
            "trend_note": trend_note,
        },
    }
