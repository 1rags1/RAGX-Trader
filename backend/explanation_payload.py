"""
Structured, AI-ready context derived only from the rule engine + indicators.

This payload is the stable contract for future LLM layers: it contains facts and
pre-computed labels, not model predictions about future prices.
"""

from __future__ import annotations

from typing import Any

from backend.signal_history import describe_bollinger_context, describe_macd_state

EXPLANATION_PAYLOAD_VERSION = 1

# Bands apply to directional (buy/sell) conviction; neutral uses neutral_stance.
_CONF_LOW_MAX = 39
_CONF_MED_MAX = 69


def _confidence_band(signal: str, confidence: int) -> str:
    sig = str(signal or "").strip().lower()
    if sig == "neutral":
        return "neutral_stance"
    c = max(0, min(100, int(confidence)))
    if c <= _CONF_LOW_MAX:
        return "low"
    if c <= _CONF_MED_MAX:
        return "medium"
    return "high"


def _ema_last(snap: dict[str, Any]) -> float | None:
    co = snap.get("chart_overlays") or {}
    lines = (co.get("lines") or {}).get("ema_20") or []
    if not lines:
        return None
    v = lines[-1].get("value")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f


def _trend_block(snap: dict[str, Any]) -> dict[str, Any]:
    lc = snap.get("last_close")
    ema = _ema_last(snap)
    try:
        close_f = float(lc) if lc is not None else float("nan")
    except (TypeError, ValueError):
        close_f = float("nan")
    if ema is None or close_f != close_f:
        return {
            "direction": "unknown",
            "price_vs_ema_20": "unknown",
            "last_close": round(close_f, 6) if close_f == close_f else None,
            "ema_20": None,
        }
    rel = (close_f - ema) / ema if ema else 0.0
    eps = 0.0006
    if rel > eps:
        pv = "above"
        direction = "up"
    elif rel < -eps:
        pv = "below"
        direction = "down"
    else:
        pv = "near"
        direction = "sideways"
    return {
        "direction": direction,
        "price_vs_ema_20": pv,
        "last_close": round(close_f, 2),
        "ema_20": round(ema, 6),
    }


def _structure_context(strategies: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not strategies:
        return None
    for s in strategies:
        if not isinstance(s, dict):
            continue
        if str(s.get("id") or "") != "price_structure":
            continue
        return {
            "strategy_id": "price_structure",
            "signal": s.get("signal"),
            "direction": s.get("direction"),
            "summary": s.get("explanation"),
            "detail": s.get("explanation_detail"),
        }
    return None


def _leg_summaries(strategies: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not strategies:
        return []
    out: list[dict[str, Any]] = []
    for s in strategies:
        if not isinstance(s, dict):
            continue
        out.append(
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "signal": s.get("signal"),
                "confidence": s.get("confidence"),
                "contribution": s.get("contribution"),
                "direction": s.get("direction"),
            }
        )
    return out


def _recent_markers(markers: list[dict[str, Any]] | None, *, limit: int) -> list[dict[str, Any]]:
    if not markers:
        return []
    combined = [m for m in markers if isinstance(m, dict) and m.get("strategy_source") == "combined_signal"]
    try:
        combined.sort(key=lambda x: int(x.get("timestamp") or 0))
    except (TypeError, ValueError):
        pass
    tail = combined[-limit:] if limit > 0 else []
    out: list[dict[str, Any]] = []
    for m in tail:
        out.append(
            {
                "action": m.get("action"),
                "timestamp": m.get("timestamp"),
                "timeframe": m.get("timeframe"),
                "confidence": m.get("confidence"),
                "explanation_excerpt": m.get("explanation_text"),
            }
        )
    return out


def build_explanation_payload(
    *,
    symbol: str,
    timeframe: str,
    indicator_snap: dict[str, Any],
    strategy: dict[str, Any],
    max_recent_markers: int = 8,
) -> dict[str, Any]:
    """
    Assemble a single JSON-serializable object for humans, LLMs, and audits.

    All fields are derived from existing engine output — no external AI calls.
    """
    final = strategy.get("final") or {}
    strategies = strategy.get("strategies") if isinstance(strategy.get("strategies"), list) else []
    markers = strategy.get("signal_markers") if isinstance(strategy.get("signal_markers"), list) else []
    plan = strategy.get("suggested_trade_plan")

    sig = str(final.get("signal") or "neutral").strip().lower()
    try:
        conf = int(final.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0
    conf = max(0, min(100, conf))

    macd = indicator_snap.get("macd") if isinstance(indicator_snap.get("macd"), dict) else {}
    bb = indicator_snap.get("bollinger") if isinstance(indicator_snap.get("bollinger"), dict) else {}
    lc = indicator_snap.get("last_close")

    net = final.get("net_score")
    net_f: float | None
    try:
        net_f = float(net) if net is not None else None
    except (TypeError, ValueError):
        net_f = None

    plan_out: dict[str, Any] | None
    if isinstance(plan, dict):
        plan_out = dict(plan)
    elif plan is None:
        plan_out = None
    else:
        plan_out = {"raw": plan}

    return {
        "schema_version": EXPLANATION_PAYLOAD_VERSION,
        "symbol": symbol.strip().upper() if isinstance(symbol, str) else "",
        "timeframe": timeframe.strip() if isinstance(timeframe, str) else "",
        "as_of_candle_time": indicator_snap.get("as_of_candle_time"),
        "bars_used": indicator_snap.get("bars_used"),
        "sufficient_data": bool(strategy.get("sufficient_data")),
        "signal": sig if sig in ("buy", "sell", "neutral") else "neutral",
        "confidence": conf,
        "confidence_band": _confidence_band(sig, conf),
        "net_score": round(net_f, 4) if net_f is not None else None,
        "trend": _trend_block(indicator_snap),
        "key_indicators": {
            "rsi_14": indicator_snap.get("rsi_14"),
            "macd": {
                "line": macd.get("line"),
                "signal": macd.get("signal"),
                "histogram": macd.get("histogram"),
            },
            "bollinger": {
                "upper": bb.get("upper"),
                "middle": bb.get("middle"),
                "lower": bb.get("lower"),
            },
            "macd_state_text": describe_macd_state(macd),
            "bollinger_context_text": describe_bollinger_context(bb, float(lc) if lc is not None else None),
        },
        "structure_context": _structure_context(strategies),
        "strategy_legs": _leg_summaries(strategies),
        "recent_signal_context": _recent_markers(markers, limit=max_recent_markers),
        "suggested_trade_plan": plan_out,
        "engine": {
            "explanation": final.get("explanation"),
            "explanation_detail": final.get("explanation_detail"),
        },
        "rendering_hints": {
            "builtin_renderer_id": "explanation_formatter_v1",
            "facts_only": True,
            "do_not_predict_price": True,
            "source": "rule_engine",
        },
    }
