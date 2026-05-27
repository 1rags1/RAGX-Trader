"""
Deterministic copy from `explanation_payload` — swap for an LLM renderer later.

Callers should treat `beginner_explanation` and `short_summary` as UX text only;
the numeric facts live in `explanation_payload` on the strategy object.
"""

from __future__ import annotations

from typing import Any, Callable

# Optional hook: same signature as format_explanation_display for tests / LLM injection.
ExplanationTextRenderer = Callable[[dict[str, Any]], dict[str, Any]]


def _plan_sentence(plan: dict[str, Any] | None, signal: str) -> str:
    if not plan or signal not in ("buy", "sell"):
        return "No suggested entry/stop/target grid is shown for neutral signals."
    side = "long" if signal == "buy" else "short"
    e = plan.get("entry")
    s = plan.get("stop_loss")
    t = plan.get("take_profit")
    if e is None and s is None and t is None:
        return "A suggested trade plan is attached with entry, stop, and target levels (education only)."
    return (
        f"The built-in plan sketches a hypothetical {side}: entry near {e}, "
        f"stop near {s}, target near {t} (education only, not an order)."
    )


def format_explanation_display(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return human-readable strings with tone keyed off `confidence_band` and signal.

    - Low directional confidence → stronger caution / “do not treat as a trigger”.
    - High directional confidence → clearer “what the rules currently say” without promising outcomes.
    """
    sig = str(payload.get("signal") or "neutral").lower()
    band = str(payload.get("confidence_band") or "medium")
    conf = int(payload.get("confidence") or 0)
    sufficient = bool(payload.get("sufficient_data"))
    trend = payload.get("trend") or {}
    trend_dir = str(trend.get("direction") or "unknown")
    pv = str(trend.get("price_vs_ema_20") or "unknown")
    ki = payload.get("key_indicators") or {}
    rsi = ki.get("rsi_14")
    macd_txt = str(ki.get("macd_state_text") or "")
    bb_txt = str(ki.get("bollinger_context_text") or "")
    struct = payload.get("structure_context") or {}
    struct_line = (
        str(struct.get("summary") or "").strip()
        if isinstance(struct, dict)
        else ""
    )

    eng = payload.get("engine") or {}
    core_expl = str(eng.get("explanation") or "").strip()

    recent = payload.get("recent_signal_context") or []
    recent_n = len(recent) if isinstance(recent, list) else 0
    last_act = None
    if recent_n and isinstance(recent[-1], dict):
        last_act = recent[-1].get("action")

    plan = payload.get("suggested_trade_plan")
    plan = plan if isinstance(plan, dict) else None

    # --- Trend / indicator paragraph (beginner) ---
    ind_parts = []
    if rsi is not None:
        ind_parts.append(f"RSI(14) is near {rsi}.")
    if macd_txt:
        ind_parts.append(macd_txt)
    if bb_txt:
        ind_parts.append(bb_txt)
    if trend_dir in ("up", "down", "sideways") and pv != "unknown":
        ind_parts.append(
            f"Price is {pv.replace('_', ' ')} the 20-period EMA on this chart — "
            f"that reads as a {trend_dir} lean in this rule set."
        )
    if struct_line:
        ind_parts.append(f"Swing structure: {struct_line}")
    indicator_paragraph = " ".join(ind_parts) if ind_parts else "Indicator details are still filling in."

    if not sufficient:
        beginner = (
            "The chart has not finished loading all indicator windows yet. "
            "When RSI, MACD, and Bollinger are ready, the checklist below will summarize what the rules see. "
            "Until then, treat any directional hint as incomplete."
        )
        summary = "Indicators warming up — wait for full data before reading the signal."
        tone = "strong_warning"
        briefing = (
            "facts_only: sufficient_data=false; no full indicator snapshot; do not infer price direction."
        )
        return {
            "beginner_explanation": beginner,
            "short_summary": summary,
            "guidance_tone": tone,
            "for_llm_briefing": briefing,
        }

    # --- Directional tone ---
    warning_prefix = ""
    action_note = ""
    if sig in ("buy", "sell") and band == "low":
        warning_prefix = (
            "Heads up: the combined score points toward a possible "
            f"{sig.upper()}, but confidence is only {conf}/100 — the rules disagree enough "
            "that this should be treated as weak context, not a green light. "
        )
    elif sig in ("buy", "sell") and band == "high":
        action_note = (
            f"Across the weighted legs, conviction is relatively high ({conf}/100): "
            f"the engine is clearly labeling this bar as a possible {sig.upper()}. "
            "That still describes what the rules see today — it is not a forecast. "
        )
    elif sig in ("buy", "sell"):
        warning_prefix = (
            f"The system tilts {sig.upper()} with moderate confidence ({conf}/100). "
            "Use it as one input among many. "
        )

    if sig == "neutral":
        neutral_note = (
            f"The combined call is NEUTRAL (confidence in that call: {conf}/100). "
            "That means the checklist does not see a clean, aligned buy or sell right now. "
        )
        beginner = (
            neutral_note + indicator_paragraph + " "
            + _plan_sentence(plan, sig)
            + " "
            + (core_expl if core_expl else "")
        ).strip()
        summary = f"Neutral — rules see no strong aligned {payload.get('timeframe') or ''} bias (conf {conf}).".strip()
        tone = "caution"
        briefing = (
            f"facts_only: signal=neutral confidence={conf}; net_score={payload.get('net_score')}; "
            f"trend={trend_dir}; last_marker_action={last_act}; do_not_predict_price."
        )
        return {
            "beginner_explanation": beginner,
            "short_summary": summary,
            "guidance_tone": tone,
            "for_llm_briefing": briefing,
        }

    beginner = (
        warning_prefix
        + action_note
        + indicator_paragraph
        + " "
        + _plan_sentence(plan, sig)
        + " "
        + (core_expl if core_expl else "")
    ).strip()

    if band == "low":
        tone = "strong_warning"
        summary = (
            f"Weak {sig.upper()} hint ({conf}/100) on {payload.get('timeframe') or '?'} — "
            "rules are split; avoid treating this as a strong trigger."
        )
    elif band == "high":
        tone = "confident_action"
        summary = (
            f"Rules currently favor {sig.upper()} with high checklist conviction ({conf}/100); "
            "still descriptive only, not a guaranteed move."
        )
    else:
        tone = "balanced"
        summary = (
            f"Moderate {sig.upper()} bias ({conf}/100) from the weighted engine on "
            f"{payload.get('timeframe') or 'this timeframe'}."
        )

    briefing = (
        f"facts_only: signal={sig} confidence={conf} band={band}; net_score={payload.get('net_score')}; "
        f"trend={trend_dir} price_vs_ema={pv}; rsi={rsi}; "
        f"recent_combined_markers={recent_n}; last_marker_action={last_act}; "
        "explain_only_do_not_predict_future."
    )

    return {
        "beginner_explanation": beginner,
        "short_summary": summary,
        "guidance_tone": tone,
        "for_llm_briefing": briefing,
    }
