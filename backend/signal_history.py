"""
Structured durable log of final trading signals (RAGX-Trader).

Records one row per chart marker event (combined BUY / SELL / EXIT) — not every
internal strategy refresh. SQLite supports ad-hoc SQL analysis later.

Duplicate prevention:
  - UNIQUE(signal_id) ties each row to the marker id from SignalMarkerStore.
  - UNIQUE(symbol, timeframe, candle_time_unix, signal_type) guards against
    replays if the marker id were ever regenerated for the same event.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS signal_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL DEFAULT 1,
  signal_id TEXT NOT NULL UNIQUE,
  decision_utc TEXT NOT NULL,
  candle_time_unix INTEGER,
  symbol TEXT,
  timeframe TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  confidence INTEGER,
  price REAL,
  explanation TEXT,
  explanation_detail TEXT,
  strategies_json TEXT NOT NULL,
  trend_ema_20 REAL,
  rsi_14 REAL,
  macd_line REAL,
  macd_signal REAL,
  macd_histogram REAL,
  macd_state TEXT,
  bb_upper REAL,
  bb_middle REAL,
  bb_lower REAL,
  bollinger_context TEXT,
  UNIQUE (symbol, timeframe, candle_time_unix, signal_type)
);
CREATE INDEX IF NOT EXISTS idx_signal_history_tf_time
  ON signal_history(timeframe, candle_time_unix);
CREATE INDEX IF NOT EXISTS idx_signal_history_symbol_time
  ON signal_history(symbol, candle_time_unix);

CREATE TABLE IF NOT EXISTS backtest_closed_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_utc TEXT NOT NULL,
  symbol TEXT NOT NULL,
  interval TEXT NOT NULL,
  signal_id TEXT,
  side TEXT,
  entry_time_unix INTEGER,
  exit_time_unix INTEGER,
  cost_adjusted_pnl REAL NOT NULL,
  outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backtest_closed_sym_iv_id
  ON backtest_closed_trades(symbol, interval, id DESC);
"""


def _last_ema_20(indicator_snap: dict[str, Any]) -> float | None:
    co = indicator_snap.get("chart_overlays") or {}
    lines = (co.get("lines") or {}).get("ema_20") or []
    if not lines:
        return None
    v = lines[-1].get("value")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def describe_macd_state(macd: dict[str, Any] | None) -> str:
    """Plain-language MACD summary (aligned with dashboard copy)."""
    if not macd:
        return "MACD not available."
    line = macd.get("line")
    sig = macd.get("signal")
    hist = macd.get("histogram")
    if any(x is None for x in (line, sig, hist)):
        return "MACD parts are not all available on this bar yet."
    try:
        h = float(hist)
        l = float(line)
        s = float(sig)
    except (TypeError, ValueError):
        return "MACD parts are not all available on this bar yet."
    if h > 0 and l > s:
        return "Momentum leaning up: MACD line above signal, positive histogram."
    if h < 0 and l < s:
        return "Momentum leaning down: MACD line below signal, negative histogram."
    if h > 0:
        return "Momentum nudging up: histogram positive."
    if h < 0:
        return "Momentum nudging down: histogram negative."
    return "MACD balanced: no strong push either way."


def describe_bollinger_context(bb: dict[str, Any] | None, last_close: float | None) -> str:
    """Plain-language band position vs close."""
    if not bb:
        return "Bollinger bands not available."
    up, mid, lo = bb.get("upper"), bb.get("middle"), bb.get("lower")
    if any(x is None for x in (up, mid, lo)):
        return "Band levels are not ready yet."
    try:
        u, m, l = float(up), float(mid), float(lo)
        c = float(last_close) if last_close is not None else float("nan")
    except (TypeError, ValueError):
        return "Band levels are not ready yet."
    span = u - l
    if span <= 0:
        return "Price inside the volatility envelope."
    if c != c:
        return "Bands show a volatility envelope around the middle line."
    tu = u - span * 0.08
    tl = l + span * 0.08
    if c >= tu:
        return "Price near the upper band — strong recent upside stretch."
    if c <= tl:
        return "Price near the lower band — strong recent downside stretch."
    return "Price between the bands — calmer vs recent volatility."


@dataclass(frozen=True)
class SignalHistoryRow:
    signal_id: str
    decision_utc: str
    candle_time_unix: int | None
    symbol: str | None
    timeframe: str
    signal_type: str
    confidence: int | None
    price: float | None
    explanation: str | None
    explanation_detail: str | None
    strategies_json: str
    trend_ema_20: float | None
    rsi_14: float | None
    macd_line: float | None
    macd_signal: float | None
    macd_histogram: float | None
    macd_state: str
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    bollinger_context: str


def build_row_from_marker(
    marker: dict[str, Any],
    *,
    decision_utc: str,
    indicator_snap: dict[str, Any],
    strategy_payload: dict[str, Any],
    price_at_signal: float | None,
) -> SignalHistoryRow:
    """Flatten marker + snapshots into one DB row."""
    final = strategy_payload.get("final") or {}
    strategies = strategy_payload.get("strategies") or []
    macd = indicator_snap.get("macd") or {}
    bb = indicator_snap.get("bollinger") or {}

    sid = str(marker.get("signal_id") or marker.get("id") or "").strip()
    if not sid:
        raise ValueError("marker missing signal_id/id")

    st = str(marker.get("action") or "").strip().lower()
    tf = str(marker.get("timeframe") or "").strip() or "1m"
    sym = marker.get("symbol")
    sym_s = sym.strip().upper() if isinstance(sym, str) and sym.strip() else None
    ct = marker.get("timestamp")
    try:
        cti = int(ct) if ct is not None else None
    except (TypeError, ValueError):
        cti = None

    conf_m = marker.get("confidence")
    try:
        ci = int(conf_m) if conf_m is not None else None
    except (TypeError, ValueError):
        ci = None

    expl = str(marker.get("explanation_text") or final.get("explanation") or "") or None
    expl_d = final.get("explanation_detail")
    expl_d_s = str(expl_d) if expl_d is not None and str(expl_d).strip() else None

    rsi = indicator_snap.get("rsi_14")
    try:
        rsi_f = float(rsi) if rsi is not None else None
    except (TypeError, ValueError):
        rsi_f = None

    def _mf(key: str) -> float | None:
        v = macd.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    ema = _last_ema_20(indicator_snap)
    lc = indicator_snap.get("last_close")
    try:
        lc_f = float(lc) if lc is not None else None
    except (TypeError, ValueError):
        lc_f = None

    price = price_at_signal if price_at_signal is not None else lc_f

    def _bf(key: str) -> float | None:
        v = bb.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return SignalHistoryRow(
        signal_id=sid,
        decision_utc=decision_utc,
        candle_time_unix=cti,
        symbol=sym_s,
        timeframe=tf,
        signal_type=st,
        confidence=ci,
        price=price,
        explanation=expl,
        explanation_detail=expl_d_s,
        strategies_json=json.dumps(strategies, separators=(",", ":"), default=str),
        trend_ema_20=ema,
        rsi_14=rsi_f,
        macd_line=_mf("line"),
        macd_signal=_mf("signal"),
        macd_histogram=_mf("histogram"),
        macd_state=describe_macd_state(macd),
        bb_upper=_bf("upper"),
        bb_middle=_bf("middle"),
        bb_lower=_bf("lower"),
        bollinger_context=describe_bollinger_context(bb, lc_f),
    )


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _ensure_schema(db_path: Path) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(CREATE_SQL)
        conn.commit()
    finally:
        conn.close()


class SignalHistoryRepository:
    """
    Append-only signal history. Use asyncio.Lock + asyncio.to_thread around writes;
    reads use a fresh connection (WAL allows concurrent readers).
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        _ensure_schema(db_path)

    def persist_marker_batch(
        self,
        markers: list[dict[str, Any]],
        *,
        decision_utc: str,
        indicator_snap: dict[str, Any],
        strategy_payload: dict[str, Any],
        price_at_signal: float | None,
    ) -> int:
        """
        Insert rows for markers emitted this evaluation. Returns count actually inserted
        (excludes duplicates blocked by UNIQUE constraints).
        """
        if not markers:
            return 0
        conn = _connect(self._path)
        try:
            inserted = 0
            cur = conn.cursor()
            for m in markers:
                try:
                    row = build_row_from_marker(
                        m,
                        decision_utc=decision_utc,
                        indicator_snap=indicator_snap,
                        strategy_payload=strategy_payload,
                        price_at_signal=price_at_signal,
                    )
                except ValueError:
                    continue
                sym_db = row.symbol if row.symbol else ""
                try:
                    cur.execute(
                        """
                        INSERT INTO signal_history (
                          schema_version, signal_id, decision_utc, candle_time_unix,
                          symbol, timeframe, signal_type, confidence, price,
                          explanation, explanation_detail, strategies_json,
                          trend_ema_20, rsi_14,
                          macd_line, macd_signal, macd_histogram, macd_state,
                          bb_upper, bb_middle, bb_lower, bollinger_context
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            SCHEMA_VERSION,
                            row.signal_id,
                            row.decision_utc,
                            row.candle_time_unix,
                            sym_db,
                            row.timeframe,
                            row.signal_type,
                            row.confidence,
                            row.price,
                            row.explanation,
                            row.explanation_detail,
                            row.strategies_json,
                            row.trend_ema_20,
                            row.rsi_14,
                            row.macd_line,
                            row.macd_signal,
                            row.macd_histogram,
                            row.macd_state,
                            row.bb_upper,
                            row.bb_middle,
                            row.bb_lower,
                            row.bollinger_context,
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    continue
            conn.commit()
            return inserted
        finally:
            conn.close()

    def fetch_recent(
        self,
        limit: int = 200,
        *,
        actions: list[str] | None = None,
        timeframe: str | None = None,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(int(limit), 2000))
        allowed_act = frozenset({"buy", "sell", "exit", "neutral"})
        act_params: list[str] = []
        if actions:
            for a in actions:
                if not a or not isinstance(a, str):
                    continue
                al = a.strip().lower()
                if al in allowed_act:
                    act_params.append(al)
        where_clauses: list[str] = []
        params: list[Any] = []
        if act_params:
            ph = ",".join("?" * len(act_params))
            where_clauses.append(f"LOWER(signal_type) IN ({ph})")
            params.extend(act_params)
        if timeframe and str(timeframe).strip():
            where_clauses.append("timeframe = ?")
            params.append(str(timeframe).strip())
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = f"""
                SELECT signal_id, decision_utc, candle_time_unix, symbol, timeframe,
                       signal_type, confidence, price, explanation, explanation_detail,
                       strategies_json, trend_ema_20, rsi_14,
                       macd_line, macd_signal, macd_histogram, macd_state,
                       bb_upper, bb_middle, bb_lower, bollinger_context
                FROM signal_history
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """
        params.append(lim)
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30.0)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql, params)
            out: list[dict[str, Any]] = []
            for r in cur.fetchall():
                d = dict(r)
                raw = d.pop("strategies_json", "[]")
                try:
                    d["strategies"] = json.loads(raw) if isinstance(raw, str) else []
                except json.JSONDecodeError:
                    d["strategies"] = []
                out.append(d)
            return out
        finally:
            conn.close()

    def fetch_recent_signal_aggregates(
        self,
        limit: int = 100,
    ) -> dict[str, Any]:
        """
        Last ``limit`` rows by id (newest first in subquery): counts and average confidence
        per signal_type (lowercase). Types with zero rows are omitted from tallies.
        """
        lim = max(1, min(int(limit), 2000))
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30.0)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                WITH recent AS (
                  SELECT signal_type, confidence
                  FROM signal_history
                  ORDER BY id DESC
                  LIMIT ?
                )
                SELECT LOWER(TRIM(signal_type)) AS st,
                       COUNT(*) AS cnt,
                       AVG(confidence) AS avg_conf
                FROM recent
                GROUP BY LOWER(TRIM(signal_type))
                """,
                (lim,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()

        tallies: dict[str, dict[str, Any]] = {}
        for st, cnt, avg_conf in rows:
            key = str(st or "").strip().lower() or "unknown"
            ac = float(avg_conf) if avg_conf is not None else None
            tallies[key] = {"count": int(cnt), "avg_confidence": ac}
        return {"limit": lim, "by_type": tallies}

    def replace_backtest_trades(
        self,
        *,
        run_utc: str,
        symbol: str,
        interval: str,
        trades: list[dict[str, Any]],
    ) -> int:
        """
        Replace stored trades for this symbol/interval with the latest backtest run.
        """
        sym = symbol.strip().upper()
        iv = interval.strip()
        conn = _connect(self._path)
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM backtest_closed_trades WHERE symbol = ? AND interval = ?",
                (sym, iv),
            )
            inserted = 0
            for t in trades:
                if not isinstance(t, dict):
                    continue
                try:
                    pnl = float(t.get("cost_adjusted_pnl", t.get("pnl", 0.0)) or 0.0)
                except (TypeError, ValueError):
                    pnl = 0.0
                oc = str(t.get("outcome") or "").strip().lower() or "unknown"
                cur.execute(
                    """
                    INSERT INTO backtest_closed_trades (
                      run_utc, symbol, interval, signal_id, side,
                      entry_time_unix, exit_time_unix, cost_adjusted_pnl, outcome
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_utc,
                        sym,
                        iv,
                        str(t.get("signal_id") or "") or None,
                        str(t.get("side") or "") or None,
                        int(t["entry_time_unix"]) if t.get("entry_time_unix") is not None else None,
                        int(t["exit_time_unix"]) if t.get("exit_time_unix") is not None else None,
                        pnl,
                        oc,
                    ),
                )
                inserted += 1
            conn.commit()
            return inserted
        finally:
            conn.close()

    def fetch_backtest_win_rate(
        self,
        *,
        symbol: str,
        interval: str,
        last_n: int = 50,
    ) -> dict[str, Any] | None:
        """Win rate over the last ``last_n`` closed trades for symbol/interval, if any."""
        sym = symbol.strip().upper()
        iv = interval.strip()
        n = max(1, min(int(last_n), 500))
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=30.0)
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT outcome, cost_adjusted_pnl
                FROM backtest_closed_trades
                WHERE symbol = ? AND interval = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (sym, iv, n),
            )
            fetched = cur.fetchall()
        finally:
            conn.close()
        if not fetched:
            return None
        wins = 0
        losses = 0
        breakeven = 0
        for outcome, pnl in fetched:
            o = str(outcome or "").strip().lower()
            if o == "win":
                wins += 1
            elif o == "loss":
                losses += 1
            elif o == "breakeven":
                breakeven += 1
            else:
                try:
                    p = float(pnl or 0.0)
                except (TypeError, ValueError):
                    p = 0.0
                if p > 0:
                    wins += 1
                elif p < 0:
                    losses += 1
                else:
                    breakeven += 1
        total = len(fetched)
        denom = wins + losses
        rate = (wins / denom) if denom else None
        return {
            "symbol": sym,
            "interval": iv,
            "trades_considered": total,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": rate,
        }
