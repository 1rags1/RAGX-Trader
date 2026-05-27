"""
Integration sanity: synthetic OHLCV, regime detection, weighted strategies, trade plan,
and walk-forward backtest with mocked REST candles.

Run from repo root:
  .venv\\Scripts\\python.exe -m pytest tests/test_integration.py -q
  .venv\\Scripts\\python.exe tests/test_integration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from typing import Any
from unittest.mock import patch

import numpy as np
import pandas as pd

from backend.backtest_engine import run_backtest
from backend.indicators import compute_indicator_snapshot
from backend.regime_detector import detect_market_regime, get_regime_adjusted_weights
from backend.strategy_engine import evaluate_dashboard_strategy
from backend.trade_plan import build_suggested_trade_plan
from backend.weighted_signal_engine import run_weighted_strategies

_FIXED_SEED = 42
_N_BARS = 100
_BASE_CLOSE = 65_000.0
# ~2% std of log returns per bar (volatile BTC-like path); small upward drift.
_VOL_LOG = 0.02
_DRIFT_LOG = 0.0004


def synthetic_btc_ohlcv_candles(
    n: int = _N_BARS,
    *,
    seed: int = _FIXED_SEED,
) -> list[dict[str, Any]]:
    """Random walk in log-price with drift, starting near ``_BASE_CLOSE``; ~``_VOL_LOG`` volatility."""
    rng = np.random.default_rng(seed)
    log_close = float(np.log(_BASE_CLOSE))
    closes: list[float] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []

    for _ in range(n):
        log_close = log_close + rng.normal(_DRIFT_LOG, _VOL_LOG)
        c = float(np.exp(log_close))
        o = closes[-1] if closes else c
        bar_range = abs(rng.normal(0, 0.003)) * c
        h = max(o, c) + bar_range * rng.uniform(0.3, 1.0)
        l = min(o, c) - bar_range * rng.uniform(0.3, 1.0)
        if l <= 0:
            l = c * 0.999
        h = max(h, o, c)
        l = min(l, o, c)
        closes.append(c)
        opens.append(o)
        highs.append(h)
        lows.append(l)

    t0 = 1_700_000_000
    interval_sec = 60
    out: list[dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "time": t0 + i * interval_sec,
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(rng.uniform(1.0, 100.0)),
                "is_final": True,
            }
        )
    return out


def candles_to_dataframe(candles: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(candles)


def test_integration_synthetic_pipeline() -> None:
    # Ensure the FastAPI module graph still imports (no syntax / circular import regressions).
    import backend.main  # noqa: F401

    raw = synthetic_btc_ohlcv_candles(_N_BARS, seed=_FIXED_SEED)
    assert len(raw) == _N_BARS
    df = candles_to_dataframe(raw)
    regime = detect_market_regime(df)
    assert regime in ("trending", "ranging", "unknown")

    snap = compute_indicator_snapshot(df)
    w_trend = get_regime_adjusted_weights("trending")
    w_range = get_regime_adjusted_weights("ranging")
    strat_t, _final_t = run_weighted_strategies(df, snap, weights=w_trend)
    strat_r, _final_r = run_weighted_strategies(df, snap, weights=w_range)
    assert isinstance(strat_t, list) and isinstance(strat_r, list)
    assert len(strat_t) == len(strat_r)

    payload = evaluate_dashboard_strategy(
        df,
        snap,
        timeframe="1m",
        symbol="BTCUSDT",
        marker_store=None,
    )
    assert "final" in payload and isinstance(payload["final"], dict)
    assert "strategies" in payload and isinstance(payload["strategies"], list)

    plan = build_suggested_trade_plan(
        df,
        signal="buy",
        sufficient_data=bool(snap.get("sufficient_data")),
    )
    assert plan is not None
    assert str(plan.get("side") or "").lower() == "buy"

    def fake_fetch_history(
        _spot_symbol: str,
        _logical_interval: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), len(raw)))
        return raw[-lim:]

    with patch("backend.backtest_engine.fetch_history_candles", fake_fetch_history):
        bt = run_backtest(symbol="BTCUSDT", interval="1m", limit=_N_BARS)

    assert bt.get("ok") is True
    metrics = bt.get("metrics") or {}
    assert isinstance(metrics.get("total_return"), float)
    assert metrics["total_trades"] > 0, (
        "expected synthetic fixture to close trades; adjust _FIXED_SEED / volatility if this fails"
    )
    assert metrics["total_return"] < metrics["raw_total_return"], (
        "cost-adjusted aggregate should be strictly below raw price PnL when costs apply"
    )


if __name__ == "__main__":
    test_integration_synthetic_pipeline()
    print("integration sanity ok")
