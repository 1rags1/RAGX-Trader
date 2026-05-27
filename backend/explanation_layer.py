"""
Glue between the rule engine payload and text renderers (builtin today, LLM later).

Usage:
    enriched = apply_explanations(strategy_dict, indicator_snap, symbol="BTCUSDT", timeframe="5m")

Later, pass `text_renderer=` with a callable that accepts `explanation_payload` and returns the same
keys as `format_explanation_display` (`beginner_explanation`, `short_summary`, `guidance_tone`,
`for_llm_briefing`) so the API shape stays stable while copy comes from an LLM.
"""

from __future__ import annotations

from typing import Any

from backend.explanation_formatter import ExplanationTextRenderer, format_explanation_display
from backend.explanation_payload import build_explanation_payload


def apply_explanations(
    strategy: dict[str, Any],
    indicator_snap: dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
    max_recent_markers: int = 8,
    text_renderer: ExplanationTextRenderer | None = None,
) -> dict[str, Any]:
    """
    Return a shallow copy of `strategy` with `explanation_payload` and `explanation_display`.

    `text_renderer`, when provided, must accept the payload dict and return a dict with at least:
    beginner_explanation, short_summary, guidance_tone, for_llm_briefing.
    """
    payload = build_explanation_payload(
        symbol=symbol,
        timeframe=timeframe,
        indicator_snap=indicator_snap,
        strategy=strategy,
        max_recent_markers=max_recent_markers,
    )
    renderer = text_renderer or format_explanation_display
    display = renderer(payload)
    return {
        **strategy,
        "explanation_payload": payload,
        "explanation_display": display,
    }
