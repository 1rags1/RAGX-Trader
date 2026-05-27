"""
Suggested trade plans for dashboard BUY/SELL only — education, not execution.

Rule-based levels from recent OHLC: entry ≈ last close, stop beyond a short swing,
take-profit sized to a fixed reward:risk (default 2:1). Optional ATR-based position
sizing metadata (risk fraction of notional vs stop distance) may be included for
display only. No orders are sent.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd
import pandas_ta_classic as ta

# Bars used to find a recent swing low (buy) or swing high (sell).
SWING_LOOKBACK = 16
# Minimum bars before suggesting a plan.
MIN_BARS_PLAN = SWING_LOOKBACK + 5
# Reward multiple of risk (risk = |entry − stop|).
TARGET_REWARD_RISK = 2.0
# Minimum acceptable R:R if we ever clamp targets (floor).
MIN_REWARD_RISK = 1.5
# Cap stop distance as fraction of entry (avoid absurdly wide stops in UI).
MAX_STOP_FRACTION_OF_ENTRY = 0.035  # 3.5%

POSITION_SIZING_DISCLAIMER = (
    "Position sizing is educational only. Size according to your own risk tolerance."
)


def compute_position_size(
    df: pd.DataFrame,
    entry: float,
    stop: float,
    risk_fraction: float = 0.01,
) -> dict[str, Any] | None:
    """
    Volatility-aware sizing snapshot: units of the asset to hold if risking ``risk_fraction``
    of ``entry`` (quote notional per 1 unit) against ``|entry - stop|`` price risk, with ATR(14) context.
    """
    if df is None or df.empty:
        return None
    need = {"high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return None

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    atr_s = ta.atr(high=high, low=low, close=close, length=14)
    if atr_s is None or len(atr_s) < 1:
        return None
    atr_raw = atr_s.iloc[-1]
    if atr_raw is None or pd.isna(atr_raw):
        return None
    try:
        atr_last = float(atr_raw)
    except (TypeError, ValueError):
        return None
    if atr_last <= 0 or math.isnan(atr_last) or math.isinf(atr_last):
        return None

    ent = float(entry)
    risk_amount = ent * float(risk_fraction)
    price_risk = abs(ent - float(stop))
    if price_risk <= 0:
        return None

    position_size = risk_amount / price_risk
    atr_multiple = price_risk / atr_last

    return {
        "position_size_units": round(position_size, 6),
        "risk_fraction": risk_fraction,
        "risk_amount": round(risk_amount, 2),
        "price_risk": round(price_risk, 6),
        "atr_14": round(atr_last, 6),
        "atr_multiple": round(atr_multiple, 2),
        "sizing_note": (
            f"Risking {risk_fraction * 100:.1f}% of notional. Stop is {atr_multiple:.1f}x ATR away."
        ),
    }


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v <= 0:  # NaN / non-positive
        return None
    return v


def _round_price(x: float) -> float:
    ax = abs(x)
    if ax >= 1000:
        return round(x, 2)
    if ax >= 1:
        return round(x, 4)
    return round(x, 6)


def _buffer_below_price(df: pd.DataFrame, last_close: float) -> float:
    """Small cushion under a swing so the stop is not exactly on the wick."""
    tail = df.tail(min(6, len(df)))
    if tail.empty:
        return max(last_close * 0.00025, 1e-8)
    try:
        ranges = (pd.to_numeric(tail["high"], errors="coerce") - pd.to_numeric(tail["low"], errors="coerce")).abs()
        mr = float(ranges.mean())
    except Exception:
        mr = 0.0
    pct = last_close * 0.00035
    return max(mr * 0.15, pct, last_close * 1e-7)


def build_suggested_trade_plan(
    df: pd.DataFrame | None,
    *,
    signal: str,
    sufficient_data: bool,
    risk_fraction: float = 0.01,
) -> dict[str, Any] | None:
    """
    Return a JSON-serializable plan for buy/sell, or None for neutral / bad data.

    Buy:  entry ≈ last close; stop just under the lowest low in the last SWING_LOOKBACK bars.
    Sell: entry ≈ last close; stop just above the highest high in the same window.
    Target: entry ± TARGET_REWARD_RISK * risk so reward:risk ≈ TARGET_REWARD_RISK : 1.

    When ATR(14) is available, includes ``position_sizing`` (units vs ``risk_fraction`` of notional).
    """
    sig = str(signal or "").strip().lower()
    if sig not in ("buy", "sell"):
        return None
    if not sufficient_data:
        return None
    if df is None or df.empty or len(df) < MIN_BARS_PLAN:
        return None
    need = {"open", "high", "low", "close"}
    if not need.issubset(set(df.columns)):
        return None

    close_s = pd.to_numeric(df["close"], errors="coerce")
    high_s = pd.to_numeric(df["high"], errors="coerce")
    low_s = pd.to_numeric(df["low"], errors="coerce")
    lc = _finite(close_s.iloc[-1])
    if lc is None:
        return None

    window = df.tail(SWING_LOOKBACK)
    lows = pd.to_numeric(window["low"], errors="coerce")
    highs = pd.to_numeric(window["high"], errors="coerce")
    swing_low = _finite(lows.min())
    swing_high = _finite(highs.max())
    if swing_low is None or swing_high is None:
        return None

    buf = _buffer_below_price(df, lc)
    entry = _round_price(lc)

    if sig == "buy":
        raw_stop = swing_low - buf
        stop = _round_price(raw_stop)
        if stop >= entry:
            stop = _round_price(entry * 0.997)  # fallback ~0.3% if swing hugs close
        risk = entry - stop
        if risk <= 0:
            return None
        if risk / entry > MAX_STOP_FRACTION_OF_ENTRY:
            stop = _round_price(entry * (1.0 - MAX_STOP_FRACTION_OF_ENTRY))
            risk = entry - stop
            if risk <= 0:
                return None
        tp = _round_price(entry + TARGET_REWARD_RISK * risk)
        reward = tp - entry
        swing_desc = (
            f"The stop sits a little below the recent swing low near {_round_price(swing_low)} "
            f"so a normal dip does not knock you out immediately."
        )
    else:
        raw_stop = swing_high + buf
        stop = _round_price(raw_stop)
        if stop <= entry:
            stop = _round_price(entry * 1.003)
        risk = stop - entry
        if risk <= 0:
            return None
        if risk / entry > MAX_STOP_FRACTION_OF_ENTRY:
            stop = _round_price(entry * (1.0 + MAX_STOP_FRACTION_OF_ENTRY))
            risk = stop - entry
            if risk <= 0:
                return None
        tp = _round_price(entry - TARGET_REWARD_RISK * risk)
        reward = entry - tp
        swing_desc = (
            f"The stop sits a little above the recent swing high near {_round_price(swing_high)} "
            f"so a quick spike against you has defined room."
        )

    if risk <= 0 or reward <= 0:
        return None
    rr = reward / risk
    if rr + 1e-6 < MIN_REWARD_RISK:
        return None

    risk_pct = (risk / entry) * 100.0 if entry else 0.0
    wide_note = ""
    if risk_pct > 2.2:
        wide_note = (
            f" The stop is about {risk_pct:.1f}% from entry — relatively wide for this snapshot; "
            "treat this as a teaching layout, not a firm recommendation."
        )

    side_word = "long" if sig == "buy" else "short"
    rr_show = round(float(rr), 2)
    summary = (
        f"Hypothetical {side_word} idea only — we do not place trades. "
        f"Entry is framed near the last close (~{entry}). "
        f"The target is sized so reward is about {rr_show} times the amount risked to the stop (after rounding prices)."
    )
    detail = (
        f"{swing_desc} The engine first aims for about {TARGET_REWARD_RISK:.1f}:1, and keeps the layout only if "
        f"the rounded prices still look at least {MIN_REWARD_RISK:.1f}:1.{wide_note}"
    )

    base_disclaimer = (
        "This is not financial advice and not an order. RAGX-Trader does not auto-trade or connect to your exchange."
    )

    plan: dict[str, Any] = {
        "side": sig,
        "entry": entry,
        "stop_loss": stop,
        "take_profit": tp,
        "risk_reward_ratio": round(float(rr), 2),
        "risk_price": _round_price(risk),
        "reward_price": _round_price(reward),
        "risk_percent_of_entry": round(risk_pct, 3),
        "summary_plain": summary,
        "detail_plain": detail,
        "disclaimer_plain": f"{base_disclaimer} {POSITION_SIZING_DISCLAIMER}",
    }

    sizing = compute_position_size(df, float(entry), float(stop), risk_fraction)
    if sizing is not None:
        plan["position_sizing"] = sizing

    return plan
