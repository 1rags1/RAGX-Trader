# Changelog

## [0.2.0] — 2026-04-28 — Regime-aware strategy engine

### Added

- **ADX-based market regime detection** — labels the latest bar as `trending`, `ranging`, or `unknown` (transition / insufficient data).
- **Dynamic weight adjustment** — strategy leg weights and vote thresholds follow the detected regime.
- **Realistic backtest costs** — **0.1%** fee + **0.05%** slippage per side on round trips (see `backend/backtest_sim.py`); metrics expose cost-adjusted vs raw totals.
- **Volatility-based position sizing** — optional ATR-based sizing metadata in suggested trade plans (display only).
- **Risk-adjusted timeframe comparison** — `GET /api/backtest/compare` ranks allowed intervals with Sharpe-like / drawdown-aware scoring for dashboard research.

### Configuration

- **No new environment variables.** `RAGX_BINANCE_REGION` (`com`, `us`, or `auto`) is unchanged.

### Breaking changes

- **None** — backward compatible.

### Usage

- Run **`GET /api/backtest/compare`** to see which timeframe performs best under current market conditions (uses the active dashboard symbol and historical REST candles).
