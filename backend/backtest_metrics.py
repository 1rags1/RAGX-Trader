"""
Performance metrics from a list of closed backtest trades (pure functions).

Aggregates (totals, equity curve, drawdown, win/loss dollar sums) use ``cost_adjusted_pnl``;
``compute_metrics`` also returns ``total_fees_paid``, ``total_slippage_paid`` (estimated per
``round_trip_cost_components``), and ``raw_total_return`` (sum of price-only ``pnl``).
"""

from __future__ import annotations

from typing import Any

from backend.backtest_sim import ClosedBacktestTrade, round_trip_cost_components


def compute_metrics(closed: list[ClosedBacktestTrade]) -> dict[str, Any]:
    n = len(closed)
    if n == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakevens": 0,
            "win_rate": None,
            "loss_rate": None,
            "average_win": None,
            "average_loss": None,
            "profit_factor": None,
            "profit_factor_is_infinite": False,
            "max_drawdown": None,
            "total_return": 0.0,
            "average_confidence_win": None,
            "average_confidence_loss": None,
            "average_confidence_breakeven": None,
            "open_at_end": 0,
            "total_fees_paid": 0.0,
            "total_slippage_paid": 0.0,
            "raw_total_return": 0.0,
        }

    wins = [t for t in closed if t.outcome == "win"]
    losses = [t for t in closed if t.outcome == "loss"]
    bes = [t for t in closed if t.outcome == "breakeven"]

    win_pnls = [t.cost_adjusted_pnl for t in wins]
    loss_pnls = [t.cost_adjusted_pnl for t in losses]

    sum_win = sum(win_pnls) if win_pnls else 0.0
    sum_loss = sum(loss_pnls) if loss_pnls else 0.0

    profit_factor: float | None
    profit_factor_is_infinite = False
    if sum_loss < 0:
        profit_factor = sum_win / abs(sum_loss)
    elif sum_loss == 0 and sum_win > 0:
        profit_factor = None
        profit_factor_is_infinite = True
    else:
        profit_factor = None

    # Equity curve: cumulative PnL in exit order (closed list should be chronological)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(closed, key=lambda x: x.exit_time_unix):
        equity += t.cost_adjusted_pnl
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)

    total_fees = 0.0
    total_slip = 0.0
    for t in closed:
        fees, slip = round_trip_cost_components(t.entry_price, t.exit_price)
        total_fees += fees
        total_slip += slip

    raw_total_return = sum(t.pnl for t in closed)

    def avg_conf(trades: list[ClosedBacktestTrade]) -> float | None:
        if not trades:
            return None
        return sum(t.confidence for t in trades) / len(trades)

    return {
        "total_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(bes),
        "win_rate": round(len(wins) / n, 4),
        "loss_rate": round(len(losses) / n, 4),
        "average_win": round(sum_win / len(wins), 6) if wins else None,
        "average_loss": round(sum_loss / len(losses), 6) if losses else None,
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "profit_factor_is_infinite": profit_factor_is_infinite,
        "max_drawdown": round(max_dd, 6),
        "total_return": round(sum(t.cost_adjusted_pnl for t in closed), 6),
        "average_confidence_win": round(avg_conf(wins), 2) if wins else None,
        "average_confidence_loss": round(avg_conf(losses), 2) if losses else None,
        "average_confidence_breakeven": round(avg_conf(bes), 2) if bes else None,
        "open_at_end": 0,
        "total_fees_paid": round(total_fees, 6),
        "total_slippage_paid": round(total_slip, 6),
        "raw_total_return": round(raw_total_return, 6),
    }


def compute_equity_curve(closed: list[ClosedBacktestTrade]) -> list[dict[str, Any]]:
    """Cumulative PnL after each closed trade, ordered by exit time."""
    if not closed:
        return []
    ordered = sorted(closed, key=lambda x: x.exit_time_unix)
    eq = 0.0
    out: list[dict[str, Any]] = []
    for t in ordered:
        eq += t.cost_adjusted_pnl
        out.append({"exit_time_unix": int(t.exit_time_unix), "equity": round(eq, 6)})
    return out


def compute_strategy_contributions(closed: list[ClosedBacktestTrade]) -> list[dict[str, Any]]:
    """
    For each weighted leg, count trades where that leg's signal matched the trade direction
    at entry, and tally win/loss/breakeven. Used to see which legs lined up with outcomes.
    """
    acc: dict[str, dict[str, Any]] = {}
    for tr in closed:
        side = str(tr.side or "").lower()
        legs = getattr(tr, "strategies_at_entry", ()) or ()
        for leg in legs:
            if str(leg.get("signal") or "").lower() != side:
                continue
            rid = str(leg.get("id") or "unknown")
            name = str(leg.get("name") or rid)
            row = acc.setdefault(
                rid,
                {
                    "strategy_id": rid,
                    "strategy_name": name,
                    "aligned_trades": 0,
                    "aligned_wins": 0,
                    "aligned_losses": 0,
                    "aligned_breakevens": 0,
                },
            )
            row["strategy_name"] = name
            row["aligned_trades"] += 1
            if tr.outcome == "win":
                row["aligned_wins"] += 1
            elif tr.outcome == "loss":
                row["aligned_losses"] += 1
            else:
                row["aligned_breakevens"] += 1

    out: list[dict[str, Any]] = []
    for row in acc.values():
        n = int(row["aligned_trades"])
        w = int(row["aligned_wins"])
        row["win_rate_when_aligned"] = round(w / n, 4) if n else None
        out.append(row)

    out.sort(
        key=lambda r: (
            -(r.get("win_rate_when_aligned") or 0),
            -int(r["aligned_trades"]),
        )
    )
    return out
