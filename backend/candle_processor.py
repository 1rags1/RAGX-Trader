"""
Candle integrity and normalization — single processing layer before chart/broadcast.

All historical batches and live ticks pass through here so buffers and the frontend see
one schema: unix open time (seconds), float OHLCV, bool is_final, typical_price.

Debug (toggle on/off):
  RAGX_CANDLE_PROCESSOR_DEBUG=1  — detailed INFO logs (reject/stale/sequence).
  RAGX_CANDLE_PIPELINE=1           — batch summaries (compat with earlier flag).
  RAGX_CANDLE_ALIGN_DEBUG=1        — first/last/active open times + UTC grid checks vs interval_sec.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Literal

from backend.indicators import enrich_candle_for_debug

logger = logging.getLogger(__name__)

_DEBUG = os.environ.get("RAGX_CANDLE_PROCESSOR_DEBUG", "").strip().lower() in ("1", "true", "yes")
_PIPELINE = os.environ.get("RAGX_CANDLE_PIPELINE", "").strip().lower() in ("1", "true", "yes")
_ALIGN = os.environ.get("RAGX_CANDLE_ALIGN_DEBUG", "").strip().lower() in ("1", "true", "yes")


def _proc_log(msg: str, *args: Any) -> None:
    if _DEBUG or _PIPELINE:
        logger.info(msg, *args)


def pipeline_log(msg: str, *args: Any) -> None:
    """Backward-compatible; respects PROCESSOR_DEBUG or CANDLE_PIPELINE."""
    _proc_log(msg, *args)


def align_debug_enabled() -> bool:
    return _ALIGN


def log_align_batch(label: str, candles: list[dict[str, Any]], interval_sec: int | None) -> None:
    """First/last open times + UTC grid check (enable with RAGX_CANDLE_ALIGN_DEBUG=1)."""
    if not _ALIGN or not candles:
        return
    times = [int(c["time"]) for c in candles]
    first_t, last_t = times[0], times[-1]
    logger.info(
        "[candle_align] batch %s count=%s interval_sec=%s first_open=%s last_open=%s (active=last)",
        label,
        len(candles),
        interval_sec,
        first_t,
        last_t,
    )
    if interval_sec and interval_sec > 0:
        bad_idx = [(i, times[i]) for i in range(len(times)) if times[i] % interval_sec != 0]
        if bad_idx:
            logger.warning(
                "[candle_align] batch %s UTC grid mismatch (t %% %s != 0): count=%s sample=%s",
                label,
                interval_sec,
                len(bad_idx),
                bad_idx[:5],
            )
        else:
            logger.info(
                "[candle_align] batch %s all open times on %ss UTC grid",
                label,
                interval_sec,
            )


def log_align_live(
    *,
    label: str,
    logical_interval: str,
    open_time: int,
    tail_time_before: int | None,
    interval_sec: int,
    is_final: bool,
) -> None:
    if not _ALIGN:
        return
    on_grid = interval_sec <= 0 or (open_time % interval_sec == 0)
    logger.info(
        "[candle_align] live tf=%s open_time=%s tail_before=%s interval_sec=%s "
        "is_final=%s same_bar_refresh=%s utc_grid_ok=%s",
        logical_interval,
        open_time,
        tail_time_before,
        interval_sec,
        is_final,
        tail_time_before is not None and open_time == tail_time_before,
        on_grid,
    )
    if not on_grid:
        logger.warning(
            "[candle_align] live open_time=%s not on %ss UTC grid (remainder=%s)",
            open_time,
            interval_sec,
            open_time % interval_sec if interval_sec else None,
        )


_STALE_BARS_BEHIND_TAIL = 64
_FUTURE_SKEW_SEC = 120


def normalize_candle(raw: dict[str, Any]) -> dict[str, Any] | None:
    """
    Canonical candle: time (int sec), open, high, low, close, volume, is_final, typical_price.
    Brackets high/low to contain open/close. Returns None if malformed.
    """
    try:
        t = int(raw["time"])
        if t <= 0:
            _proc_log("[candle_processor] reject: non-positive time %s", t)
            return None
        o = float(raw["open"])
        h = float(raw["high"])
        lo = float(raw["low"])
        c = float(raw["close"])
        v = float(raw.get("volume", 0.0))
        is_final = bool(raw.get("is_final", True))
        for x in (o, h, lo, c):
            if x != x or x <= 0:
                _proc_log("[candle_processor] reject: non-finite or non-positive OHLC")
                return None
        if h < lo:
            _proc_log("[candle_processor] reject: high < low")
            return None
        h = max(h, o, c)
        lo = min(lo, o, c)
        base: dict[str, Any] = {
            "time": t,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
            "is_final": is_final,
        }
        return enrich_candle_for_debug(base)
    except (KeyError, TypeError, ValueError) as e:
        _proc_log("[candle_processor] reject: parse error %s", e)
        return None


def is_valid_candle(c: dict[str, Any]) -> bool:
    """Fast check without relying on side effects; equivalent to normalize_candle is not None."""
    return normalize_candle(c) is not None


def sort_dedupe_by_time(candles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Ascending by time; duplicate timestamps keep last row. Returns (out, duplicate_overwrites)."""
    if not candles:
        return [], 0
    by_t: dict[int, dict[str, Any]] = {}
    dup = 0
    for c in candles:
        t = int(c["time"])
        if t in by_t:
            dup += 1
        by_t[t] = c
    out = sorted(by_t.values(), key=lambda x: int(x["time"]))
    return out, dup


def validate_candle_sequence(
    candles: list[dict[str, Any]],
    interval_sec: int | None = None,
) -> list[str]:
    """
    Return human-readable warnings (non-fatal). Optional interval_sec checks step alignment.
    For aggregated 10m bars, pass interval_sec=600 or None to skip step checks.
    """
    warns: list[str] = []
    if len(candles) < 2:
        return warns
    for i in range(1, len(candles)):
        t0, t1 = int(candles[i - 1]["time"]), int(candles[i]["time"])
        if t1 <= t0:
            warns.append(f"non_increasing_time idx={i} {t0}->{t1}")
        elif interval_sec and interval_sec > 0 and (t1 - t0) % interval_sec != 0:
            warns.append(
                f"misaligned_step idx={i} delta={t1 - t0} interval={interval_sec} t0={t0} t1={t1}"
            )
    return warns


def upsert_candle_by_timestamp(
    bars: list[dict[str, Any]],
    candle: dict[str, Any],
) -> tuple[list[dict[str, Any]], Literal["rejected", "insert", "replace"]]:
    """
    Merge one candle into a time-ordered list by open time (replace if exists).
    `candle` is normalized before merge.
    """
    n = normalize_candle(candle)
    if n is None:
        return bars, "rejected"
    t = int(n["time"])
    by_time = {int(x["time"]): dict(x) for x in bars}
    action: Literal["insert", "replace"] = "replace" if t in by_time else "insert"
    by_time[t] = n
    out = [by_time[k] for k in sorted(by_time)]
    return out, action


def merge_historical_and_live_candles(
    historical: list[dict[str, Any]],
    live: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize all inputs, concatenate, sort, dedupe (last wins)."""
    rows_in: list[dict[str, Any]] = []
    for c in historical:
        n = normalize_candle(c)
        if n is not None:
            rows_in.append(n)
    live_list = [live] if isinstance(live, dict) else list(live)
    for c in live_list:
        n = normalize_candle(c)
        if n is not None:
            rows_in.append(n)
    out, _ = sort_dedupe_by_time(rows_in)
    return out


def is_clearly_stale_live(ct: int, tail_time: int | None, interval_sec: int) -> bool:
    """
    True if a *new* live open_time is far behind the buffer tail (not an update to tail).
    Same timestamp as tail is never stale (forming-candle refresh).
    """
    if tail_time is None:
        return False
    if ct == tail_time:
        return False
    iv = max(int(interval_sec), 1)
    if ct < tail_time - _STALE_BARS_BEHIND_TAIL * iv:
        _proc_log(
            "[candle_processor] stale: open_time=%s tail=%s threshold=%ss",
            ct,
            tail_time,
            _STALE_BARS_BEHIND_TAIL * iv,
        )
        return True
    return False


def process_live_candle(
    raw: dict[str, Any],
    *,
    tail_time: int | None,
    interval_sec: int,
    logical_interval: str | None = None,
) -> dict[str, Any] | None:
    """
    Normalize + reject future + reject clearly stale vs tail. Used before buffer upsert.
    """
    c = normalize_candle(raw)
    if c is None:
        return None
    ct = int(c["time"])
    now = int(time.time())
    if ct > now + _FUTURE_SKEW_SEC:
        _proc_log("[candle_processor] reject future open_time=%s now=%s", ct, now)
        return None
    if is_clearly_stale_live(ct, tail_time, interval_sec):
        return None
    log_align_live(
        label="accepted",
        logical_interval=logical_interval or "?",
        open_time=ct,
        tail_time_before=tail_time,
        interval_sec=interval_sec,
        is_final=bool(c.get("is_final")),
    )
    return c


def process_historical_batch(
    raw: list[dict[str, Any]],
    *,
    label: str,
    interval_sec: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Normalize each row, drop malformed, sort, dedupe, validate sequence (warnings only).
    """
    normalized: list[dict[str, Any]] = []
    rejected = 0
    for x in raw:
        n = normalize_candle(x)
        if n is None:
            rejected += 1
            continue
        normalized.append(n)
    final, dup_ov = sort_dedupe_by_time(normalized)
    warns = validate_candle_sequence(final, interval_sec)
    for w in warns:
        _proc_log("[candle_processor] sequence_warn %s: %s", label, w)
    if label:
        log_history_batch(label, final)
    log_align_batch(label or "unnamed_batch", final, interval_sec)
    stats = {
        "rejected_malformed": rejected,
        "duplicate_overwrites": dup_ov,
        "sequence_warnings": len(warns),
        "output_count": len(final),
    }
    return final, stats


def dedupe_sort_candles(
    candles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """
    Backward-compatible: normalize + sort/dedupe. Second value = rows removed (malformed + dup).
    """
    if not candles:
        return [], 0
    raw_len = len(candles)
    final, stats = process_historical_batch(candles, label="", interval_sec=None)
    removed = raw_len - len(final)
    return final, removed


def log_history_batch(label: str, candles: list[dict[str, Any]]) -> None:
    if not label:
        return
    if not candles:
        pipeline_log("[candle_processor] %s: empty", label)
        return
    ts = [int(c["time"]) for c in candles]
    pipeline_log(
        "[candle_processor] %s: count=%s first_ts=%s last_ts=%s",
        label,
        len(candles),
        ts[0],
        ts[-1],
    )


def to_chart_payload(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lightweight Charts OHLC + time only (already-normalized candles)."""
    out: list[dict[str, Any]] = []
    for c in candles:
        out.append(
            {
                "time": int(c["time"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
        )
    return out
