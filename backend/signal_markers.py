"""
Persistent signal markers for chart review.

Rule-based only — no order placement. The chart shows final combined markers only:
BUY / SELL / EXIT (no per-strategy clutter). EXIT is recorded when the combined
call returns to neutral so the prior bias is clearly “closed”; flips buy→sell or
sell→buy emit only the new directional marker on that bar.

Markers are keyed by candle open time: at most one row per (timestamp, strategy_source, action).

Duplicate suppression: repeated BUY (or repeated SELL) on nearby bars is skipped unless
confidence rises by MEANINGFUL_CONF_INCREASE points, to avoid visual spam.

Append-only JSONL log for later audit (`data/signal_markers.jsonl` by default).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from backend.timeframes import INTERVAL_SECONDS

Action = Literal["buy", "sell", "exit"]

MARKER_VERSION = 1
MAX_MARKERS = 500
MIN_COMBINED_CONFIDENCE = 45
# Skip another BUY/SELL if same direction as last marker within this many bars (min 3 bar times or 2 min).
SAME_DIR_WINDOW_BAR_MULT = 3
SAME_DIR_WINDOW_MIN_SEC = 120
# Require at least this confidence jump to repeat the same direction inside the window.
MEANINGFUL_CONF_INCREASE = 6


def _new_id() -> str:
    return f"sm_{uuid.uuid4().hex[:12]}"


def _clip(s: str, n: int) -> str:
    t = str(s) if s is not None else ""
    return t if len(t) <= n else t[: n - 1] + "…"


class SignalMarkerStore:
    """
    In-memory ring of markers for the active symbol; visible subset is filtered
    by dataframe time range + timeframe + symbol on each API response.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._markers: list[dict[str, Any]] = []
        self._prev_final: str | None = None
        self._bound_symbol: str | None = None
        self._log_path = log_path
        self._last_ingested: list[dict[str, Any]] = []

    def clear(self) -> None:
        self._markers = []
        self._prev_final = None
        self._last_ingested = []

    def set_symbol(self, symbol: str | None) -> None:
        """Clear all markers when the traded symbol changes (new instrument)."""
        sym = symbol.strip().upper() if isinstance(symbol, str) and symbol.strip() else None
        if self._bound_symbol is not None and sym is not None and sym != self._bound_symbol:
            self.clear()
        self._bound_symbol = sym

    def _append(self, row: dict[str, Any]) -> None:
        self._markers.append(row)
        if len(self._markers) > MAX_MARKERS:
            self._markers = self._markers[-MAX_MARKERS:]
        if self._log_path is not None:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            except OSError:
                pass

    def _exists(self, timestamp: int, strategy_source: str, action: str) -> bool:
        return any(
            m["timestamp"] == timestamp
            and m["strategy_source"] == strategy_source
            and m["action"] == action
            for m in self._markers
        )

    def _last_combined_buy_sell(self, timeframe: str) -> tuple[str | None, int, int]:
        """Most recent combined BUY/SELL on this timeframe: (action, timestamp, confidence)."""
        tf = timeframe.strip() if isinstance(timeframe, str) else ""
        for m in reversed(self._markers):
            if m.get("timeframe") != tf:
                continue
            if m.get("strategy_source") != "combined_signal":
                continue
            act = str(m.get("action") or "")
            if act not in ("buy", "sell"):
                continue
            return act, int(m["timestamp"]), int(m.get("confidence") or 0)
        return None, 0, 0

    def _should_skip_duplicate_direction(
        self,
        *,
        timeframe: str,
        action: str,
        timestamp: int,
        confidence: int,
    ) -> bool:
        """
        If we already placed the same direction recently with similar confidence,
        skip (keeps chart readable). Allow repeat when confidence jumps enough or enough time passed.
        """
        prev_act, prev_t, prev_c = self._last_combined_buy_sell(timeframe)
        if prev_act != action or prev_t <= 0:
            return False
        tf = timeframe.strip() if isinstance(timeframe, str) and timeframe.strip() else "1m"
        bar_sec = int(INTERVAL_SECONDS.get(tf, 60))
        window_sec = max(bar_sec * SAME_DIR_WINDOW_BAR_MULT, SAME_DIR_WINDOW_MIN_SEC)
        dt = int(timestamp) - prev_t
        if dt >= window_sec:
            return False
        if int(confidence) >= prev_c + MEANINGFUL_CONF_INCREASE:
            return False
        return True

    def _try_add(
        self,
        *,
        timestamp: int,
        timeframe: str,
        symbol: str | None,
        action: Action,
        confidence: int,
        label: str,
        strategy_source: str,
        explanation_ref: str,
        explanation_text: str,
    ) -> None:
        if self._exists(timestamp, strategy_source, action):
            return
        mid = _new_id()
        row: dict[str, Any] = {
            "schema_version": MARKER_VERSION,
            "id": mid,
            "signal_id": mid,
            "timestamp": int(timestamp),
            "timeframe": timeframe,
            "symbol": symbol,
            "action": action,
            "confidence": max(0, min(100, int(confidence))),
            "label": _clip(label, 80),
            "strategy_source": _clip(strategy_source, 80),
            "explanation_ref": _clip(explanation_ref, 160),
            "explanation_text": _clip(explanation_text, 600),
        }
        self._append(row)
        self._last_ingested.append(dict(row))

    @property
    def last_ingested_batch(self) -> list[dict[str, Any]]:
        """Markers appended during the most recent `ingest` call (for SQLite history)."""
        return list(self._last_ingested)

    def ingest(
        self,
        df: pd.DataFrame | None,
        _strategies: list[dict[str, Any]],
        final: dict[str, Any],
        *,
        timeframe: str,
        symbol: str | None,
        sufficient_data: bool,
    ) -> None:
        """Record combined final BUY/SELL and EXIT transitions. Per-strategy votes are not stored as markers."""
        self._last_ingested = []
        self.set_symbol(symbol)
        cur_final = str((final or {}).get("signal") or "neutral")

        if df is None or df.empty or "time" not in df.columns:
            self._prev_final = cur_final
            return

        if not sufficient_data:
            self._prev_final = cur_final
            return

        last_t = int(df["time"].iloc[-1])
        tf = timeframe.strip() if isinstance(timeframe, str) and timeframe.strip() else "1m"
        sym = self._bound_symbol

        # Final combined BUY/SELL only on the chart — sub-strategy votes are not emitted as markers.
        fc = int((final or {}).get("confidence") or 0)
        expl = str((final or {}).get("explanation") or "")
        if cur_final in ("buy", "sell") and fc >= MIN_COMBINED_CONFIDENCE:
            if not self._should_skip_duplicate_direction(
                timeframe=tf, action=cur_final, timestamp=last_t, confidence=fc
            ):
                self._try_add(
                    timestamp=last_t,
                    timeframe=tf,
                    symbol=sym,
                    action=cur_final,  # type: ignore[arg-type]
                    confidence=fc,
                    label=f"Combined {cur_final.upper()}",
                    strategy_source="combined_signal",
                    explanation_ref=f"combined_signal:{last_t}",
                    explanation_text=expl,
                )

        # EXIT only when bias clears to neutral (flip to opposite side uses the new BUY/SELL only).
        prev = self._prev_final
        if prev == "buy" and cur_final == "neutral":
            self._try_add(
                timestamp=last_t,
                timeframe=tf,
                symbol=sym,
                action="exit",
                confidence=fc,
                label="Exit (buy bias closed)",
                strategy_source="combined_signal",
                explanation_ref=f"exit:{last_t}:{prev}->{cur_final}",
                explanation_text=_clip(
                    f"The prior buy call is no longer active; the system is neutral. {expl}",
                    600,
                ),
            )
        elif prev == "sell" and cur_final == "neutral":
            self._try_add(
                timestamp=last_t,
                timeframe=tf,
                symbol=sym,
                action="exit",
                confidence=fc,
                label="Exit (sell bias closed)",
                strategy_source="combined_signal",
                explanation_ref=f"exit:{last_t}:{prev}->{cur_final}",
                explanation_text=_clip(
                    f"The prior sell call is no longer active; the system is neutral. {expl}",
                    600,
                ),
            )

        self._prev_final = cur_final

    def visible_for_chart(
        self,
        df: pd.DataFrame | None,
        *,
        timeframe: str,
        symbol: str | None,
    ) -> list[dict[str, Any]]:
        """Markers whose candle time lies in the current history window and matches TF + symbol."""
        if df is None or df.empty or "time" not in df.columns:
            return []
        t_min = int(df["time"].min())
        t_max = int(df["time"].max())
        tf = timeframe.strip() if isinstance(timeframe, str) and timeframe.strip() else "1m"
        sym = symbol.strip().upper() if isinstance(symbol, str) and symbol.strip() else None
        out: list[dict[str, Any]] = []
        for m in self._markers:
            if m.get("timeframe") != tf:
                continue
            ms = m.get("symbol")
            if sym and ms and str(ms).upper() != sym:
                continue
            ts = int(m["timestamp"])
            if t_min <= ts <= t_max:
                out.append(m)
        out.sort(key=lambda x: int(x["timestamp"]))
        return out

    def all_markers(self, limit: int = 400) -> list[dict[str, Any]]:
        return list(self._markers[-limit:])
