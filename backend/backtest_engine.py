"""
Walk-forward backtest: replay historical OHLC, run the live strategy + trade plan, simulate fills.
Returns metrics and equity built from cost-adjusted PnL (fees + slippage per ``backtest_sim``).

``run_backtest_compare`` ranks allowed intervals by a Sharpe-like score (daily equity-change mean / stdev
when possible, else a return vs drawdown proxy), exposes the top three, a timeframe ``recommendation``, and
``current_regime`` from ADX on recent 5m bars.

Uses the same `evaluate_dashboard_strategy` and `build_suggested_trade_plan` path as production.
Isolated `SignalMarkerStore` (no disk). Designed so `symbol` can change later.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend.backtest_metrics import (
    compute_equity_curve,
    compute_metrics,
    compute_strategy_contributions,
)
from backend.backtest_sim import (
    ClosedBacktestTrade,
    FEE_PCT,
    SLIPPAGE_PCT,
    classify_outcome,
    exit_hit_long,
    exit_hit_short,
    pnl_for_close,
    raw_pnl_for_close,
)
from backend.candle_processor import process_historical_batch
from backend.indicators import MINIMUM_BARS_REQUIRED, compute_indicator_snapshot
from backend.regime_detector import detect_market_regime
from backend.signal_markers import SignalMarkerStore
from backend.strategy_engine import evaluate_dashboard_strategy
from backend.timeframes import (
    ALLOWED_INTERVALS,
    INTERVAL_SECONDS,
    fetch_history_candles,
    is_allowed_interval,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_WINDOW = 500
MAX_BACKTEST_LIMIT = 1000

BACKTEST_COSTS_NOTE = (
    f"Costs modeled: {FEE_PCT * 100:.2f}% fee + {SLIPPAGE_PCT * 100:.2f}% slippage per side."
)


def _equity_daily_return_series(equity_curve: list[dict[str, Any]]) -> list[float]:
    """Day-over-day changes in end-of-day cumulative equity (UTC calendar days)."""
    if not equity_curve:
        return []
    by_day: dict[str, float] = {}
    for pt in sorted(equity_curve, key=lambda x: int(x.get("exit_time_unix") or 0)):
        ts = int(pt.get("exit_time_unix") or 0)
        if ts <= 0:
            continue
        dkey = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        by_day[dkey] = float(pt.get("equity") or 0.0)
    if len(by_day) < 2:
        return []
    days_sorted = sorted(by_day.keys())
    rets: list[float] = []
    prev_eq: float | None = None
    for d in days_sorted:
        eq = by_day[d]
        if prev_eq is not None:
            rets.append(eq - prev_eq)
        prev_eq = eq
    return rets


def _compare_interval_scores(
    metrics: dict[str, Any],
    equity_curve: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Sharpe-like ranking inputs: daily equity diffs when possible, else drawdown/return ratio for vol.
    Includes a drawdown-penalized ``simple_score`` tie-breaker.
    """
    total_return = float(metrics.get("total_return") or 0.0)
    total_trades = int(metrics.get("total_trades") or 0)
    max_dd_raw = metrics.get("max_drawdown")
    max_dd_f = float(max_dd_raw) if isinstance(max_dd_raw, (int, float)) else 0.0

    return_per_trade = (total_return / total_trades) if total_trades > 0 else 0.0

    daily_rets = _equity_daily_return_series(equity_curve)
    if len(daily_rets) >= 2:
        volatility = float(statistics.stdev(daily_rets))
        mean_dr = float(statistics.mean(daily_rets))
        sharpe_like = mean_dr / (volatility + 1e-9)
    else:
        volatility = max_dd_f / (abs(total_return) + 1e-9)
        sharpe_like = total_return / (abs(volatility) + 1e-9)

    simple_score = total_return / (max_dd_f + abs(total_return) * 0.1 + 1.0)

    return {
        "return_per_trade": round(return_per_trade, 6),
        "volatility": round(volatility, 6),
        "sharpe_like": round(sharpe_like, 6),
        "simple_score": round(simple_score, 6),
    }


def _timeframe_bucket_recommendation(best_interval: str | None) -> str:
    if not best_interval:
        return "Insufficient results to recommend a timeframe."
    if best_interval in ("1m", "5m", "10m"):
        return (
            "1m/5m showed the best raw return but likely suffers from overfitting and noise. "
            "Consider 15m or 30m for live trading."
        )
    if best_interval in ("15m", "30m"):
        return "15m/30m appears optimal — balances signal quality with trade frequency."
    sec = INTERVAL_SECONDS.get(best_interval, 0)
    if best_interval == "1d" or sec >= 3600:
        return (
            "Higher timeframes (1h+) showed best risk-adjusted returns. "
            "Fewer trades but higher confidence per signal."
        )
    return "Review ranked intervals and pick a timeframe that matches your risk horizon."


def _trim_strategies(strategies: Any) -> tuple[dict[str, Any], ...]:
    """Snapshot weighted legs at entry (id, name, signal) for contribution stats."""
    if not isinstance(strategies, list):
        return ()
    out: list[dict[str, Any]] = []
    for s in strategies:
        if not isinstance(s, dict):
            continue
        out.append(
            {
                "id": str(s.get("id") or ""),
                "name": str(s.get("name") or s.get("id") or ""),
                "signal": str(s.get("signal") or "neutral").lower(),
            }
        )
    return tuple(out)


@dataclass
class _OpenLeg:
    signal_id: str
    side: str
    entry_bar_index: int
    entry_time_unix: int
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: int
    timeframe: str
    strategies_at_entry: tuple[dict[str, Any], ...] = ()


def _bars_to_dataframe(cleaned: list[dict[str, Any]]) -> pd.DataFrame:
    if not cleaned:
        return pd.DataFrame()
    return pd.DataFrame(cleaned)


def _fetch_regime_context_df(symbol: str, bar_limit: int) -> tuple[pd.DataFrame, str]:
    """Build OHLC on 5m for ``detect_market_regime`` (latest bar is the last row)."""
    iv = "5m"
    lim = max(30, min(int(bar_limit), MAX_BACKTEST_LIMIT))
    sym = symbol.strip().upper()
    raw = fetch_history_candles(sym, iv, lim)
    cleaned, _st = process_historical_batch(
        raw,
        label=f"regime_compare({sym})",
        interval_sec=INTERVAL_SECONDS[iv],
    )
    return _bars_to_dataframe(cleaned), iv


def run_backtest(
    *,
    symbol: str,
    interval: str,
    limit: int = 500,
    max_window: int = DEFAULT_MAX_WINDOW,
) -> dict[str, Any]:
    """
    Fetch closed candles from Binance REST, replay bar-by-bar, return metrics + optional trades.

    Raises ValueError for bad interval/limit.
    """
    if not is_allowed_interval(interval):
        raise ValueError("unsupported interval")
    lim = max(MINIMUM_BARS_REQUIRED + 5, min(int(limit), MAX_BACKTEST_LIMIT))
    sym = symbol.strip().upper()

    raw = fetch_history_candles(sym, interval, lim)
    cleaned, st = process_historical_batch(
        raw,
        label=f"backtest({sym},{interval})",
        interval_sec=INTERVAL_SECONDS[interval],
    )
    if st.get("rejected_malformed"):
        logger.info("Backtest candle_processor stats: %s", st)

    df = _bars_to_dataframe(cleaned)
    n = len(df)
    if n < MINIMUM_BARS_REQUIRED + 2:
        m0 = compute_metrics([])
        m0["open_at_end"] = 0
        return {
            "ok": True,
            "symbol": sym,
            "interval": interval,
            "bars_used": n,
            "metrics": m0,
            "closed_trades": [],
            "equity_curve": [],
            "strategy_contributions": [],
            "note": "Not enough bars after cleaning to backtest.",
        }

    store = SignalMarkerStore(log_path=None)
    store.set_symbol(sym)

    open_legs: list[_OpenLeg] = []
    closed: list[ClosedBacktestTrade] = []
    seen_ids: set[str] = set()

    mw = max(MINIMUM_BARS_REQUIRED + 1, min(int(max_window), n))

    for i in range(n):
        start = max(0, i + 1 - mw)
        win = df.iloc[start : i + 1]
        if len(win) < MINIMUM_BARS_REQUIRED:
            continue

        h = float(win["high"].iloc[-1])
        l = float(win["low"].iloc[-1])
        t_unix = int(win["time"].iloc[-1])

        # --- exits on bar i (only for trades entered on a prior bar) ---
        still_open: list[_OpenLeg] = []
        for leg in open_legs:
            if leg.entry_bar_index >= i:
                still_open.append(leg)
                continue
            hit = (
                exit_hit_long(h, l, leg.stop_loss, leg.take_profit)
                if leg.side == "buy"
                else exit_hit_short(h, l, leg.stop_loss, leg.take_profit)
            )
            if hit is None:
                still_open.append(leg)
                continue
            reason, px = hit
            raw_pnl = raw_pnl_for_close(leg.side, leg.entry_price, px)
            net_pnl = pnl_for_close(leg.side, leg.entry_price, px)
            oc = classify_outcome(net_pnl, leg.entry_price)
            closed.append(
                ClosedBacktestTrade(
                    signal_id=leg.signal_id,
                    side=leg.side,
                    timeframe=leg.timeframe,
                    entry_time_unix=leg.entry_time_unix,
                    entry_price=leg.entry_price,
                    stop_loss=leg.stop_loss,
                    take_profit=leg.take_profit,
                    confidence=leg.confidence,
                    exit_time_unix=t_unix,
                    exit_price=float(px),
                    exit_reason=reason,
                    outcome=oc,
                    pnl=raw_pnl,
                    cost_adjusted_pnl=net_pnl,
                    strategies_at_entry=leg.strategies_at_entry,
                )
            )
        open_legs = still_open

        snap = compute_indicator_snapshot(win)
        strat = evaluate_dashboard_strategy(
            win,
            snap,
            timeframe=interval,
            symbol=sym,
            marker_store=store,
        )

        final = strat.get("final") or {}
        plan = strat.get("suggested_trade_plan")

        for m in list(store.last_ingested_batch):
            act = str(m.get("action") or "").lower()
            if act not in ("buy", "sell"):
                continue
            if not plan or str(plan.get("side") or "").lower() != act:
                continue
            sid = str(m.get("signal_id") or m.get("id") or "")
            if not sid:
                sid = f"paper:{interval}:{t_unix}:{act}"
            if sid in seen_ids:
                continue

            ep = plan.get("entry")
            sl = plan.get("stop_loss")
            tp = plan.get("take_profit")
            try:
                entry_f = float(ep)
                stop_f = float(sl)
                tp_f = float(tp)
            except (TypeError, ValueError):
                continue

            conf = int(final.get("confidence") or 0)
            snap_legs = _trim_strategies(strat.get("strategies"))
            open_legs.append(
                _OpenLeg(
                    signal_id=sid,
                    side=act,
                    entry_bar_index=i,
                    entry_time_unix=t_unix,
                    entry_price=entry_f,
                    stop_loss=stop_f,
                    take_profit=tp_f,
                    confidence=conf,
                    timeframe=interval,
                    strategies_at_entry=snap_legs,
                )
            )
            seen_ids.add(sid)

    # mark-to-close any still open at last bar (optional) — user didn't ask; leave open uncounted in metrics
    # or close at last close for return - standard is exclude from win rate or count as open
    metrics = compute_metrics(closed)
    metrics["open_at_end"] = len(open_legs)
    eq = compute_equity_curve(closed)
    strat_c = compute_strategy_contributions(closed)

    return {
        "ok": True,
        "symbol": sym,
        "interval": interval,
        "bars_used": n,
        "bars_simulated": n - MINIMUM_BARS_REQUIRED,
        "candle_processor_stats": st,
        "metrics": metrics,
        "closed_trades": [x.to_json() for x in closed],
        "equity_curve": eq,
        "strategy_contributions": strat_c,
        "note": BACKTEST_COSTS_NOTE,
    }


def run_backtest_compare(
    *,
    symbol: str,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Run ``run_backtest`` for each allowed interval; rank by a Sharpe-like ratio (mean daily
    equity change / volatility of daily changes when enough history exists; otherwise a
    return vs drawdown proxy), then by ``simple_score`` and cost-adjusted ``total_return``.

    Returns the top three intervals, a plain-English ``recommendation`` based on the winner's
    timeframe bucket, and ``current_regime`` from ADX regime detection on the latest 5m bars.
    """
    lim = max(MINIMUM_BARS_REQUIRED + 5, min(int(limit), MAX_BACKTEST_LIMIT))
    sym = symbol.strip().upper()
    rows: list[dict[str, Any]] = []

    for iv in ALLOWED_INTERVALS:
        r = run_backtest(symbol=sym, interval=iv, limit=lim)
        m = r.get("metrics") or {}
        eq = r.get("equity_curve") or []
        scores = _compare_interval_scores(m, eq)
        tr = float(m.get("total_return") or 0.0) if isinstance(m.get("total_return"), (int, float)) else 0.0
        row = {
            "interval": iv,
            "bars_used": r.get("bars_used"),
            "note": r.get("note"),
            "metrics": {
                "total_trades": m.get("total_trades"),
                "win_rate": m.get("win_rate"),
                "profit_factor": m.get("profit_factor"),
                "profit_factor_is_infinite": m.get("profit_factor_is_infinite"),
                "max_drawdown": m.get("max_drawdown"),
                "total_return": m.get("total_return"),
                "raw_total_return": m.get("raw_total_return"),
                "total_fees_paid": m.get("total_fees_paid"),
                "total_slippage_paid": m.get("total_slippage_paid"),
            },
            **scores,
        }
        rows.append(row)

    def _sort_key(item: dict[str, Any]) -> tuple[float, float, float]:
        return (
            float(item.get("sharpe_like") or 0.0),
            float(item.get("simple_score") or 0.0),
            float((item.get("metrics") or {}).get("total_return") or 0.0),
        )

    ranked = sorted(rows, key=_sort_key, reverse=True)
    top_3 = ranked[:3]
    best_interval = top_3[0]["interval"] if top_3 else None
    best_return = None
    if top_3:
        tr_top = (top_3[0].get("metrics") or {}).get("total_return")
        best_return = float(tr_top) if isinstance(tr_top, (int, float)) else None

    regime_df, regime_iv = _fetch_regime_context_df(sym, lim)
    regime_label: str = "unknown"
    if len(regime_df) >= 20 and {"high", "low", "close"}.issubset(regime_df.columns):
        regime_label = detect_market_regime(regime_df)

    return {
        "ok": True,
        "symbol": sym,
        "limit": lim,
        "by_interval": rows,
        "ranked_intervals": ranked,
        "top_3_intervals": top_3,
        "best_interval": best_interval,
        "best_total_return": best_return,
        "recommendation": _timeframe_bucket_recommendation(best_interval),
        "current_regime": {
            "regime": regime_label,
            "based_on_interval": regime_iv,
            "bars_used": len(regime_df),
        },
        "note": BACKTEST_COSTS_NOTE,
    }
