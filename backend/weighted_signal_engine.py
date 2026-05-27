"""
Weighted scoring signal engine (RAGX-Trader)

Combines five transparent, rule-based legs into one net score in roughly [-100, +100],
then maps that net to BUY / SELL / NEUTRAL with a confidence 0–100.

Per-leg caps and buy/sell cutoffs are configurable via ``run_weighted_strategies(..., weights=)``
(see ``DEFAULT_WEIGHTS``). Pass ``None`` to keep the original defaults (leg caps sum to 100,
thresholds ±24). Regime-specific presets can be wired by callers later without changing this module.

How scoring works:
  * Each evaluator returns a signed "contribution" in [-cap, +cap] for its category.
  * Bullish evidence adds positive contribution; bearish adds negative.
  * net_score = sum(contributions). No ML—fixed rules and thresholds only.

How the final signal is chosen:
  * If net_score >= net_buy_threshold  -> BUY  (bullish evidence dominates)
  * If net_score <= net_sell_threshold -> SELL (bearish evidence dominates)
  * Otherwise                           -> NEUTRAL
  * Confidence rises when |net_score| is further past the threshold (stronger conviction).

The UI receives the same `strategies` list shape as before (signal buy/sell/neutral,
confidence, explanation, explanation_detail) plus optional fields direction, contribution,
weight_cap for debugging.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import pandas as pd
import pandas_ta_classic as ta

from backend.indicators import MINIMUM_BARS_REQUIRED, _finite_or_none

Direction = Literal["bullish", "bearish", "neutral"]

DEFAULT_WEIGHTS: dict[str, float] = {
    "ema_trend": 25.0,
    "macd_momentum": 20.0,
    "rsi_reversal": 20.0,
    "price_structure": 20.0,
    "bollinger_context": 15.0,
    "net_buy_threshold": 24.0,
    "net_sell_threshold": -24.0,
}


def _merge_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Merge caller overrides onto defaults; unknown keys ignored."""
    base = dict(DEFAULT_WEIGHTS)
    if not weights:
        return base
    for k, v in weights.items():
        if k in base:
            base[k] = float(v)
    return base

# --- RSI (reversal leg) ---
RSI_LENGTH = 14
RSI_DEEP_OVERSOLD = 28.0
RSI_DEEP_OVERBOUGHT = 72.0
RSI_EXIT_OVERSOLD_MAX = 32.0
RSI_EXIT_OVERBOUGHT_MIN = 68.0
RSI_POST_OVERSOLD_CEILING = 48.0
RSI_POST_OVERBOUGHT_FLOOR = 52.0

# --- MACD ---
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# --- Bollinger ---
BB_LENGTH = 20
BB_STD = 2.0
BB_TOUCH_EPS = 0.0008

# --- Structure: swing lookback ---
STRUCTURE_LOOKBACK = 20


def _row(
    *,
    id_: str,
    name: str,
    direction: Direction,
    contribution: float,
    weight_cap: float,
    reason: str,
    reason_detail: str,
) -> dict[str, Any]:
    """Single leg raw result before UI mapping."""
    c = max(-float(weight_cap), min(float(weight_cap), float(contribution)))
    return {
        "id": id_,
        "name": name,
        "direction": direction,
        "contribution": round(c, 4),
        "weight_cap": int(weight_cap),
        "reason": reason,
        "reason_detail": reason_detail,
    }


def _to_panel_row(leg: dict[str, Any]) -> dict[str, Any]:
    """API / sidebar shape (buy|sell|neutral + 0–100 strength for that leg)."""
    d = leg["direction"]
    sig = "buy" if d == "bullish" else "sell" if d == "bearish" else "neutral"
    cap = float(leg["weight_cap"])
    mag = abs(float(leg["contribution"]))
    conf = int(round(min(100.0, (mag / cap * 100.0) if cap > 0 else 0.0)))
    return {
        "id": leg["id"],
        "name": leg["name"],
        "signal": sig,
        "confidence": conf,
        "explanation": leg["reason"],
        "explanation_detail": leg["reason_detail"],
        "direction": d,
        "contribution": leg["contribution"],
        "weight_cap": leg["weight_cap"],
    }


def _last_ema_from_snap(snap: dict[str, Any]) -> float | None:
    lines = (snap.get("chart_overlays") or {}).get("lines") or {}
    pts = lines.get("ema_20") or []
    if not pts:
        return None
    last = pts[-1]
    if not isinstance(last, dict):
        return None
    return _finite_or_none(last.get("value"))


def eval_ema_trend(snap: dict[str, Any], weight_cap: float = 25.0) -> dict[str, Any]:
    """
    Trend leg: price vs EMA20 (same line as chart). Further above = more bullish points;
    further below = more bearish. Dead zone near the line = neutral.
    """
    cap = float(weight_cap)
    ema = _last_ema_from_snap(snap)
    lc_raw = snap.get("last_close")
    try:
        lc = float(lc_raw) if lc_raw is not None else float("nan")
    except (TypeError, ValueError):
        lc = float("nan")
    if ema is None or ema <= 0 or lc != lc:
        return _row(
            id_="ema_trend",
            name="EMA trend",
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="The trend line is not ready—no EMA score yet.",
            reason_detail="Missing last_close or ema_20 overlay point.",
        )
    rel = (lc - ema) / ema
    dead = 0.00025
    if abs(rel) <= dead:
        return _row(
            id_="ema_trend",
            name="EMA trend",
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Price is hugging the trend line—no clear edge from EMA alone.",
            reason_detail=f"Close {lc:.4f}, EMA20 {ema:.4f}, relative {rel*100:.3f}%.",
        )
    # Scale: ~0.4% away from EMA uses most of the cap
    scale = cap / 0.004
    raw = rel * scale
    contrib = max(-cap, min(cap, raw))
    direction: Direction = "bullish" if contrib > 0 else "bearish"
    if rel > 0:
        reason = "Price sits above the smoothed trend line—short-term path favors buyers on this rule."
    else:
        reason = "Price sits below the smoothed trend line—short-term path favors sellers on this rule."
    return _row(
        id_="ema_trend",
        name="EMA trend",
        direction=direction,
        contribution=contrib,
        weight_cap=cap,
        reason=reason,
        reason_detail=f"Close {lc:.4f}, EMA20 {ema:.4f}, rel {rel*100:.3f}%, scaled contribution {contrib:.2f}/{cap:.0f}.",
    )


def _macd_rows(macd_tbl: pd.DataFrame) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if macd_tbl is None or len(macd_tbl) < 2:
        return None
    return macd_tbl.iloc[-2].to_dict(), macd_tbl.iloc[-1].to_dict()


def _macd_values(row: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    line = _finite_or_none(row.get("MACD_12_26_9"))
    sig = _finite_or_none(row.get("MACDs_12_26_9"))
    hist = _finite_or_none(row.get("MACDh_12_26_9"))
    if line is None and sig is None:
        keys = [k for k in row if isinstance(k, str)]
        if len(keys) >= 3:
            line = _finite_or_none(row[keys[0]])
            sig = _finite_or_none(row[keys[1]])
            hist = _finite_or_none(row[keys[2]])
    return line, sig, hist


def eval_macd_momentum(df: pd.DataFrame, weight_cap: float = 20.0) -> dict[str, Any]:
    """Momentum leg: MACD cross and histogram alignment."""
    cap = float(weight_cap)
    sid, name = "macd_momentum", "MACD momentum"
    if df is None or len(df) < MINIMUM_BARS_REQUIRED:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Not enough bars to score momentum.",
            reason_detail="",
        )
    close = pd.to_numeric(df["close"], errors="coerce")
    macd_tbl = ta.macd(close, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    rows = _macd_rows(macd_tbl)
    if rows is None:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="MACD is not ready on this window.",
            reason_detail="",
        )
    prev_row, now_row = rows
    lp, sp, hp = _macd_values(prev_row)
    ln, sn, hn = _macd_values(now_row)
    if None in (lp, sp, ln, sn):
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="MACD line or signal missing on the latest bars.",
            reason_detail="",
        )

    if lp <= sp and ln > sn:
        return _row(
            id_=sid,
            name=name,
            direction="bullish",
            contribution=cap,
            weight_cap=cap,
            reason="Momentum turned up—the fast MACD line crossed above the signal.",
            reason_detail=f"Cross up: L {lp:.6f}→{ln:.6f}, S {sp:.6f}→{sn:.6f}.",
        )
    if lp >= sp and ln < sn:
        return _row(
            id_=sid,
            name=name,
            direction="bearish",
            contribution=-cap,
            weight_cap=cap,
            reason="Momentum turned down—the fast MACD line crossed below the signal.",
            reason_detail=f"Cross down: L {lp:.6f}→{ln:.6f}, S {sp:.6f}→{sn:.6f}.",
        )

    if hp is not None and hn is not None and not (math.isnan(hp) or math.isnan(hn)):
        if hn > hp and hn > 0 and ln > sn:
            return _row(
                id_=sid,
                name=name,
                direction="bullish",
                contribution=cap * 0.55,
                weight_cap=cap,
                reason="Upward momentum is building without a fresh crossover.",
                reason_detail=f"Histogram rising, positive; H {hp:.6f}→{hn:.6f}.",
            )
        if hn < hp and hn < 0 and ln < sn:
            return _row(
                id_=sid,
                name=name,
                direction="bearish",
                contribution=-cap * 0.55,
                weight_cap=cap,
                reason="Downward momentum is building without a fresh crossover.",
                reason_detail=f"Histogram falling, negative; H {hp:.6f}→{hn:.6f}.",
            )

    return _row(
        id_=sid,
        name=name,
        direction="neutral",
        contribution=0.0,
        weight_cap=cap,
        reason="No strong MACD thrust or crossover on this bar.",
        reason_detail="Histogram / line-signal combo in a neutral zone.",
    )


def eval_rsi_reversal(df: pd.DataFrame, weight_cap: float = 20.0) -> dict[str, Any]:
    """Reversal leg: RSI exits from stretched zones (oversold/overbought)."""
    cap = float(weight_cap)
    sid, name = "rsi_reversal", "RSI reversal"
    if df is None or len(df) < MINIMUM_BARS_REQUIRED:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Not enough bars to score RSI.",
            reason_detail="",
        )
    close = pd.to_numeric(df["close"], errors="coerce")
    rsi = ta.rsi(close, length=RSI_LENGTH)
    if rsi is None or len(rsi) < 2:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="RSI not available on these closes.",
            reason_detail="",
        )
    r_prev = _finite_or_none(rsi.iloc[-2])
    r_now = _finite_or_none(rsi.iloc[-1])
    if r_prev is None or r_now is None:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="RSI is not defined on the last two bars.",
            reason_detail="",
        )

    if r_prev < RSI_EXIT_OVERSOLD_MAX and r_now > r_prev and r_now < RSI_POST_OVERSOLD_CEILING:
        return _row(
            id_=sid,
            name=name,
            direction="bullish",
            contribution=cap * 0.9,
            weight_cap=cap,
            reason="RSI bounced up from a sold-out zone—short-term dip-buying bias.",
            reason_detail=f"RSI {r_prev:.1f}→{r_now:.1f} leaving oversold (below {RSI_EXIT_OVERSOLD_MAX}).",
        )
    if r_prev > RSI_EXIT_OVERBOUGHT_MIN and r_now < r_prev and r_now > RSI_POST_OVERBOUGHT_FLOOR:
        return _row(
            id_=sid,
            name=name,
            direction="bearish",
            contribution=-cap * 0.9,
            weight_cap=cap,
            reason="RSI slipped from an overheated zone—short-term profit-taking bias.",
            reason_detail=f"RSI {r_prev:.1f}→{r_now:.1f} leaving overbought (above {RSI_EXIT_OVERBOUGHT_MIN}).",
        )
    if r_now <= RSI_DEEP_OVERSOLD:
        return _row(
            id_=sid,
            name=name,
            direction="bullish",
            contribution=cap * 0.55,
            weight_cap=cap,
            reason="RSI is extremely low—watching for a possible snap-back.",
            reason_detail=f"RSI {r_now:.1f} ≤ deep oversold {RSI_DEEP_OVERSOLD}.",
        )
    if r_now >= RSI_DEEP_OVERBOUGHT:
        return _row(
            id_=sid,
            name=name,
            direction="bearish",
            contribution=-cap * 0.55,
            weight_cap=cap,
            reason="RSI is extremely high—watching for a possible cool-off.",
            reason_detail=f"RSI {r_now:.1f} ≥ deep overbought {RSI_DEEP_OVERBOUGHT}.",
        )

    return _row(
        id_=sid,
        name=name,
        direction="neutral",
        contribution=0.0,
        weight_cap=cap,
        reason="RSI is mid-range—no reversal trigger from this rule.",
        reason_detail=f"RSI {r_now:.1f}.",
    )


def _bb_bands(last_bb_row: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    for suf in ("2.0", "2"):
        lo = _finite_or_none(last_bb_row.get(f"BBL_20_{suf}"))
        mid = _finite_or_none(last_bb_row.get(f"BBM_20_{suf}"))
        hi = _finite_or_none(last_bb_row.get(f"BBU_20_{suf}"))
        if lo is not None or mid is not None or hi is not None:
            return lo, mid, hi
    return None, None, None


def eval_bollinger_volatility(df: pd.DataFrame, weight_cap: float = 15.0) -> dict[str, Any]:
    """Volatility context: band touches mean stretched price vs recent range."""
    cap = float(weight_cap)
    sid, name = "bollinger_context", "Bollinger context"
    if df is None or len(df) < MINIMUM_BARS_REQUIRED:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Not enough bars for band context.",
            reason_detail="",
        )
    close = pd.to_numeric(df["close"], errors="coerce")
    c_now = _finite_or_none(close.iloc[-1])
    if c_now is None:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Latest close missing.",
            reason_detail="",
        )
    bb = ta.bbands(close, length=BB_LENGTH, std=BB_STD)
    if bb is None or bb.empty:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Bollinger bands not computed.",
            reason_detail="",
        )
    last_bb = bb.iloc[-1].to_dict()
    lo, _mid, hi = _bb_bands(last_bb)
    if lo is None or hi is None:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Band edges unavailable.",
            reason_detail="",
        )
    lower_touch = c_now <= lo * (1.0 + BB_TOUCH_EPS)
    upper_touch = c_now >= hi * (1.0 - BB_TOUCH_EPS)
    if lower_touch:
        return _row(
            id_=sid,
            name=name,
            direction="bullish",
            contribution=cap * 0.85,
            weight_cap=cap,
            reason="Price is pressed against the lower band—often seen before a bounce attempt.",
            reason_detail=f"Close {c_now:.2f} vs lower {lo:.2f}, upper {hi:.2f}.",
        )
    if upper_touch:
        return _row(
            id_=sid,
            name=name,
            direction="bearish",
            contribution=-cap * 0.85,
            weight_cap=cap,
            reason="Price is pressed against the upper band—often seen before a fade attempt.",
            reason_detail=f"Close {c_now:.2f} vs upper {hi:.2f}, lower {lo:.2f}.",
        )
    span = hi - lo
    if span and span > 0:
        pos = (c_now - lo) / span
        if pos < 0.22:
            return _row(
                id_=sid,
                name=name,
                direction="bullish",
                contribution=cap * 0.25,
                weight_cap=cap,
                reason="Price is in the lower part of the band—slight dip-buying context.",
                reason_detail=f"Band position {pos*100:.0f}% above lower.",
            )
        if pos > 0.78:
            return _row(
                id_=sid,
                name=name,
                direction="bearish",
                contribution=-cap * 0.25,
                weight_cap=cap,
                reason="Price is in the upper part of the band—slight extension context.",
                reason_detail=f"Band position {pos*100:.0f}% above lower.",
            )
    return _row(
        id_=sid,
        name=name,
        direction="neutral",
        contribution=0.0,
        weight_cap=cap,
        reason="Price is away from the band extremes—no stretch signal.",
        reason_detail=f"Close {c_now:.2f}, lower {lo:.2f}, upper {hi:.2f}.",
    )


def eval_price_structure(df: pd.DataFrame, weight_cap: float = 20.0) -> dict[str, Any]:
    """
    Structure leg: last two swing highs and swing lows in a short window.
    HH + HL -> bullish; LH + LL -> bearish; mixed -> smaller or zero score.
    """
    cap = float(weight_cap)
    sid, name = "price_structure", "Price structure"
    need = max(12, STRUCTURE_LOOKBACK)
    if df is None or len(df) < need:
        return _row(
            id_=sid,
            name=name,
            direction="neutral",
            contribution=0.0,
            weight_cap=cap,
            reason="Not enough bars to read swing structure.",
            reason_detail="",
        )
    seg = df.iloc[-need:]
    highs = pd.to_numeric(seg["high"], errors="coerce").values
    lows = pd.to_numeric(seg["low"], errors="coerce").values
    n = len(highs)
    peak_idx: list[tuple[int, float]] = []
    trough_idx: list[tuple[int, float]] = []
    for i in range(1, n - 1):
        if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
            peak_idx.append((i, float(highs[i])))
        if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
            trough_idx.append((i, float(lows[i])))

    hh = hl = lh = ll = False
    if len(peak_idx) >= 2:
        hh = peak_idx[-1][1] > peak_idx[-2][1]
        lh = peak_idx[-1][1] < peak_idx[-2][1]
    if len(trough_idx) >= 2:
        hl = trough_idx[-1][1] > trough_idx[-2][1]
        ll = trough_idx[-1][1] < trough_idx[-2][1]

    bull_score = (1 if hh else 0) + (1 if hl else 0)
    bear_score = (1 if lh else 0) + (1 if ll else 0)

    if bull_score == 2 and bear_score == 0:
        return _row(
            id_=sid,
            name=name,
            direction="bullish",
            contribution=cap,
            weight_cap=cap,
            reason="Recent swings show higher highs and higher lows—structure favors buyers.",
            reason_detail=f"Last peaks {peak_idx[-2][1]:.4f}→{peak_idx[-1][1]:.4f}; troughs {trough_idx[-2][1]:.4f}→{trough_idx[-1][1]:.4f}.",
        )
    if bear_score == 2 and bull_score == 0:
        return _row(
            id_=sid,
            name=name,
            direction="bearish",
            contribution=-cap,
            weight_cap=cap,
            reason="Recent swings show lower highs and lower lows—structure favors sellers.",
            reason_detail=f"Last peaks {peak_idx[-2][1]:.4f}→{peak_idx[-1][1]:.4f}; troughs {trough_idx[-2][1]:.4f}→{trough_idx[-1][1]:.4f}.",
        )
    if bull_score > bear_score:
        return _row(
            id_=sid,
            name=name,
            direction="bullish",
            contribution=cap * 0.45,
            weight_cap=cap,
            reason="Swings lean constructive (partial higher-high / higher-low read).",
            reason_detail=f"hh={hh} hl={hl} lh={lh} ll={ll}.",
        )
    if bear_score > bull_score:
        return _row(
            id_=sid,
            name=name,
            direction="bearish",
            contribution=-cap * 0.45,
            weight_cap=cap,
            reason="Swings lean soft (partial lower-high / lower-low read).",
            reason_detail=f"hh={hh} hl={hl} lh={lh} ll={ll}.",
        )
    return _row(
        id_=sid,
        name=name,
        direction="neutral",
        contribution=0.0,
        weight_cap=cap,
        reason="Swing pattern is mixed—no clear HH/HL or LH/LL.",
        reason_detail=f"hh={hh} hl={hl} lh={lh} ll={ll}.",
    )


def _warmup_legs(w: dict[str, float]) -> list[dict[str, Any]]:
    msg = "Indicators warming up—this leg is idle until the panel is ready."
    d = "sufficient_data=false in indicator snapshot."
    caps = [
        ("ema_trend", "EMA trend", w["ema_trend"]),
        ("rsi_reversal", "RSI reversal", w["rsi_reversal"]),
        ("macd_momentum", "MACD momentum", w["macd_momentum"]),
        ("bollinger_context", "Bollinger context", w["bollinger_context"]),
        ("price_structure", "Price structure", w["price_structure"]),
    ]
    return [
        _row(
            id_=i,
            name=n,
            direction="neutral",
            contribution=0.0,
            weight_cap=float(c),
            reason=msg,
            reason_detail=d,
        )
        for i, n, c in caps
    ]


def _combine_final(
    legs: list[dict[str, Any]],
    snap: dict[str, Any] | None,
    net_buy_threshold: float = 24.0,
    net_sell_threshold: float = -24.0,
) -> dict[str, Any]:
    """
    Sum leg contributions -> net_score, apply thresholds, build explanation strings.
    """
    net = sum(float(x["contribution"]) for x in legs)
    net_r = round(net, 2)
    parts_detail = [f"{x['id']}: {x['contribution']:+g} (cap ±{x['weight_cap']})" for x in legs]
    detail = (
        f"Weighted net score: {net_r:+g} (each leg can add up to its cap; bullish adds, bearish subtracts). "
        f"BUY if net ≥ {net_buy_threshold}, SELL if net ≤ {net_sell_threshold}, else NEUTRAL. "
        f"Breakdown — " + "; ".join(parts_detail) + "."
    )

    if net >= net_buy_threshold:
        strength = min(100, max(48, int(50 + net * 0.42)))
        expl = (
            f"The weighted checklist scores clearly bullish ({net_r:+g}). "
            "Trend, momentum, reversal, structure, and band context line up enough to call a possible buy."
        )
        return {
            "signal": "buy",
            "confidence": strength,
            "explanation": expl,
            "explanation_detail": detail,
            "net_score": net_r,
        }
    if net <= net_sell_threshold:
        strength = min(100, max(48, int(50 + abs(net) * 0.42)))
        expl = (
            f"The weighted checklist scores clearly bearish ({net_r:+g}). "
            "Enough legs point to weakness or stretched conditions to call a possible sell."
        )
        return {
            "signal": "sell",
            "confidence": strength,
            "explanation": expl,
            "explanation_detail": detail,
            "net_score": net_r,
        }
    uncertainty = max(25, min(88, int(72 - abs(net) * 1.1)))
    expl = (
        f"The weighted score is near balance ({net_r:+g}), so the system stays neutral. "
        "No strong agreement across trend, momentum, reversal, structure, and bands."
    )
    return {
        "signal": "neutral",
        "confidence": uncertainty,
        "explanation": expl,
        "explanation_detail": detail,
        "net_score": net_r,
    }


def run_weighted_strategies(
    df: pd.DataFrame,
    indicator_snap: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Run all five legs and produce (sidebar_rows, final_signal_dict).

    When `sufficient_data` is false, returns idle legs and caller should override `final`
    for warmup messaging (see strategy_engine).
    """
    w = _merge_weights(weights)
    nb = w["net_buy_threshold"]
    ns = w["net_sell_threshold"]

    if not indicator_snap.get("sufficient_data"):
        legs = _warmup_legs(w)
        panel = [_to_panel_row(x) for x in legs]
        final = _combine_final(legs, indicator_snap, nb, ns)
        return panel, final

    legs = [
        eval_ema_trend(indicator_snap, w["ema_trend"]),
        eval_rsi_reversal(df, w["rsi_reversal"]),
        eval_macd_momentum(df, w["macd_momentum"]),
        eval_bollinger_volatility(df, w["bollinger_context"]),
        eval_price_structure(df, w["price_structure"]),
    ]
    panel = [_to_panel_row(x) for x in legs]
    final = _combine_final(legs, indicator_snap, nb, ns)
    return panel, final
