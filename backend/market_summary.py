"""
Beginner-friendly market snapshot from the same indicators + rule engine the dashboard already uses.

No external APIs — only `explanation_payload` fields and simple derived heuristics.
"""

from __future__ import annotations

from typing import Any

MARKET_SUMMARY_VERSION = 1

# Bollinger (upper−lower)/middle — rough volatility envelope vs price scale.
_BB_WIDE = 0.048
_BB_TIGHT = 0.022


def _bb_relative_width(ep: dict[str, Any]) -> float | None:
    ki = ep.get("key_indicators") or {}
    bb = ki.get("bollinger") or {}
    if not isinstance(bb, dict):
        return None
    try:
        u = float(bb["upper"])
        m = float(bb["middle"])
        lo = float(bb["lower"])
    except (KeyError, TypeError, ValueError):
        return None
    if m <= 0:
        return None
    return (u - lo) / m


def _trend_sentence(ep: dict[str, Any]) -> str:
    if not ep.get("sufficient_data"):
        return "Trend is not shown yet — the chart needs a few more bars for a reliable average line."
    t = ep.get("trend") or {}
    d = str(t.get("direction") or "unknown")
    pv = str(t.get("price_vs_ema_20") or "unknown")
    if d == "unknown" or pv == "unknown":
        return "Trend versus the 20-period average is unclear on this bar."
    if d == "up":
        return "Price is mostly above the short average — that reads as an upward lean on this timeframe."
    if d == "down":
        return "Price is mostly below the short average — that reads as a downward lean on this timeframe."
    return "Price is hugging the short average — there is no strong up or down lean from this line alone."


def _momentum_sentence(ep: dict[str, Any]) -> str:
    if not ep.get("sufficient_data"):
        return "Momentum (MACD-style) is not ready until the full indicator window loads."
    ki = ep.get("key_indicators") or {}
    txt = ki.get("macd_state_text")
    if isinstance(txt, str) and txt.strip():
        return txt.strip()
    return "Momentum reading is not available on this bar."


def _structure_trend_agree(ep: dict[str, Any]) -> str | None:
    """Return 'aligned', 'mixed', or None if unknown."""
    t = ep.get("trend") or {}
    td = str(t.get("direction") or "")
    sc = ep.get("structure_context")
    if not isinstance(sc, dict):
        return None
    sd = str(sc.get("direction") or "")
    if td in ("unknown", "sideways") or sd not in ("bullish", "bearish", "neutral"):
        return None
    if td == "up" and sd == "bullish":
        return "aligned"
    if td == "down" and sd == "bearish":
        return "aligned"
    if td in ("up", "down") and sd in ("bullish", "bearish"):
        return "mixed"
    return None


def _rhythm_sentence(ep: dict[str, Any]) -> str:
    if not ep.get("sufficient_data"):
        return "We will describe calm vs choppy action after bands and averages are ready."
    rw = _bb_relative_width(ep)
    ki = ep.get("key_indicators") or {}
    bb_ctx = str(ki.get("bollinger_context_text") or "").strip()
    agree = _structure_trend_agree(ep)

    parts: list[str] = []
    if rw is not None:
        if rw >= _BB_WIDE:
            parts.append(
                "Volatility bands are relatively wide — price has been swinging in a bigger envelope, "
                "which often feels choppier."
            )
        elif rw <= _BB_TIGHT:
            parts.append(
                "Volatility bands are tight — the market has been calmer or coiling in a smaller range."
            )
        else:
            parts.append(
                "Volatility is in a normal range — not extremely squeezed and not extremely stretched."
            )

    if agree == "aligned":
        parts.append(
            "The short trend line and recent swing pattern mostly agree, so the picture looks a bit cleaner to read."
        )
    elif agree == "mixed":
        parts.append(
            "The short trend line and recent swing pattern do not fully agree — that often feels more back-and-forth."
        )

    if bb_ctx:
        parts.append(bb_ctx)

    if not parts:
        return "Rhythm is summarized from Bollinger width and swing structure when both are available."
    return " ".join(parts)


def _stance_sentence(ep: dict[str, Any]) -> str:
    if not ep.get("sufficient_data"):
        return "Prefer waiting — the engine will not size up a trade idea until indicators finish warming up."
    sig = str(ep.get("signal") or "neutral").lower()
    band = str(ep.get("confidence_band") or "medium")
    conf = int(ep.get("confidence") or 0)

    if sig == "neutral":
        return (
            "Prefer waiting — the weighted rules are not lining up on a clear buy or sell right now "
            f"(the system rates that neutral read at {conf}/100)."
        )
    side = "buy" if sig == "buy" else "sell"
    if band == "low":
        return (
            f"Lean toward waiting — there is only a weak {side} tilt ({conf}/100). "
            "The system treats that as context, not a strong prompt to act."
        )
    if band == "medium":
        return (
            f"Watch closely — there is a moderate {side} bias ({conf}/100). "
            "Use it as one input; the engine is not shouting certainty."
        )
    return (
        f"The engine is leaning more actively toward a possible {side.upper()} ({conf}/100). "
        "That still describes today's checklist — it is not a forecast of the next move."
    )


def _signal_sentence(ep: dict[str, Any]) -> str:
    sig = str(ep.get("signal") or "neutral").upper()
    conf = int(ep.get("confidence") or 0)
    if not ep.get("sufficient_data"):
        return f"Current call: {sig} — confidence not meaningful until data is ready (showing {conf}/100)."
    return f"Current call: {sig} — rule strength {conf} out of 100 on this timeframe."


def _headline(ep: dict[str, Any]) -> str:
    if not ep.get("sufficient_data"):
        return "Quick read: still loading — give the chart a moment, then this summary will fill in."
    t = ep.get("trend") or {}
    d = str(t.get("direction") or "unknown")
    sig = str(ep.get("signal") or "neutral").lower()
    conf = int(ep.get("confidence") or 0)

    if d == "up":
        trend_bit = "Price is leaning up versus its short average."
    elif d == "down":
        trend_bit = "Price is leaning down versus its short average."
    elif d == "sideways":
        trend_bit = "Price is hugging its short average — no strong lean."
    else:
        trend_bit = "Trend versus the average is unclear here."

    if sig == "buy":
        call_bit = f"The system’s combined call is BUY ({conf}/100)."
    elif sig == "sell":
        call_bit = f"The system’s combined call is SELL ({conf}/100)."
    else:
        call_bit = f"The system’s combined call is NEUTRAL ({conf}/100 confidence in that stance)."

    return f"{trend_bit} {call_bit}"


def build_market_summary(strategy: dict[str, Any]) -> dict[str, Any]:
    """
    Build a small JSON object for the sidebar “market at a glance” block.

    Expects `strategy` to already include `explanation_payload` (after `apply_explanations`).
    """
    ep = strategy.get("explanation_payload")
    if not isinstance(ep, dict):
        return {
            "schema_version": MARKET_SUMMARY_VERSION,
            "headline": "Market summary is not available for this response.",
            "items": [],
            "plain_all": "Market summary is not available for this response.",
        }

    items: list[dict[str, str]] = [
        {"id": "trend", "title": "Trend", "text": _trend_sentence(ep)},
        {"id": "momentum", "title": "Momentum", "text": _momentum_sentence(ep)},
        {"id": "rhythm", "title": "Calm or choppy?", "text": _rhythm_sentence(ep)},
        {"id": "stance", "title": "Wait or act?", "text": _stance_sentence(ep)},
        {"id": "signal", "title": "System call", "text": _signal_sentence(ep)},
    ]
    headline = _headline(ep)
    plain_all = headline + " " + " ".join(f"{it['title']}: {it['text']}" for it in items)

    return {
        "schema_version": MARKET_SUMMARY_VERSION,
        "headline": headline,
        "items": items,
        "plain_all": plain_all,
    }
