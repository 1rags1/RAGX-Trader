"""
Trade simulation for backtests — mirrors frontend paper-trading exit rules.

Conservative same-bar rule: if stop and target both touch, assume stop first.
PnL per 1 unit base; breakeven uses same epsilon scale as paper mode.

Each round trip applies a simple cost model: Binance-style spot fee per notional side
and symmetric slippage on entry plus exit (see ``FEE_PCT``, ``SLIPPAGE_PCT``). Stored
trades keep raw price PnL in ``pnl`` and after-cost PnL in ``cost_adjusted_pnl``;
outcomes and aggregate metrics use the cost-adjusted figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Outcome = Literal["win", "loss", "breakeven"]

BE_EPS_FRAC = 0.00008

FEE_PCT = 0.001  # 0.1% per side (Binance Spot default)
SLIPPAGE_PCT = 0.0005  # 0.05% slippage on entry and exit


def round_trip_cost_components(entry_price: float, exit_price: float) -> tuple[float, float]:
    """
    Return ``(fee_cost, slippage_cost)`` for one unit round trip (same basis as ``apply_costs``).
    Invalid prices → ``(0.0, 0.0)``.
    """
    if entry_price <= 0 or exit_price <= 0:
        return (0.0, 0.0)
    notional_leg_sum = float(entry_price) + float(exit_price)
    return (notional_leg_sum * FEE_PCT, notional_leg_sum * SLIPPAGE_PCT)


def apply_costs(pnl: float, entry_price: float, exit_price: float) -> float:
    """
    Subtract fee and slippage from raw per-unit PnL.

    Fee and slippage are each charged on (entry_price + exit_price) × rate, matching
    a full round-trip notional on both legs.
    """
    if entry_price <= 0 or exit_price <= 0:
        return float(pnl)
    fee_cost, slippage_cost = round_trip_cost_components(entry_price, exit_price)
    return float(pnl) - fee_cost - slippage_cost


def raw_pnl_for_close(side: str, entry: float, exit_px: float) -> float:
    """Price-only PnL for 1 unit before fees and slippage."""
    if side == "buy":
        return float(exit_px) - float(entry)
    return float(entry) - float(exit_px)


def pnl_for_close(side: str, entry: float, exit_px: float) -> float:
    """Net PnL after ``apply_costs`` (same units as ``raw_pnl_for_close``)."""
    return apply_costs(raw_pnl_for_close(side, entry, exit_px), entry, exit_px)


def classify_outcome(pnl: float, entry: float) -> Outcome:
    if entry and abs(pnl) < abs(float(entry)) * BE_EPS_FRAC:
        return "breakeven"
    if pnl > 0:
        return "win"
    if pnl < 0:
        return "loss"
    return "breakeven"


def exit_hit_long(high: float, low: float, stop: float, take_profit: float) -> tuple[str, float] | None:
    hit_stop = low <= stop
    hit_tp = high >= take_profit
    if hit_stop and hit_tp:
        return ("stop", stop)
    if hit_stop:
        return ("stop", stop)
    if hit_tp:
        return ("target", take_profit)
    return None


def exit_hit_short(high: float, low: float, stop: float, take_profit: float) -> tuple[str, float] | None:
    hit_stop = high >= stop
    hit_tp = low <= take_profit
    if hit_stop and hit_tp:
        return ("stop", stop)
    if hit_stop:
        return ("stop", stop)
    if hit_tp:
        return ("target", take_profit)
    return None


@dataclass
class ClosedBacktestTrade:
    signal_id: str
    side: str
    timeframe: str
    entry_time_unix: int
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: int
    exit_time_unix: int
    exit_price: float
    exit_reason: str
    outcome: Outcome
    pnl: float
    cost_adjusted_pnl: float
    strategies_at_entry: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "side": self.side,
            "timeframe": self.timeframe,
            "entry_time_unix": self.entry_time_unix,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "confidence": self.confidence,
            "exit_time_unix": self.exit_time_unix,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "outcome": self.outcome,
            "pnl": round(self.pnl, 8),
            "cost_adjusted_pnl": round(self.cost_adjusted_pnl, 8),
        }


if __name__ == "__main__":
    r = raw_pnl_for_close("buy", 100.0, 101.0)
    n = pnl_for_close("buy", 100.0, 101.0)
    assert r == 1.0
    assert n < r
    assert apply_costs(1.0, 100.0, 101.0) == n
    assert classify_outcome(n, 100.0) in ("win", "loss", "breakeven")
    print("backtest_sim sanity ok", r, n)
