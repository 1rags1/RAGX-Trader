"""
Normalize Finnhub insider transactions for Investor Dashboard (SEC Form 4 context).
Display-only — not a trading signal.
"""

from __future__ import annotations

from typing import Any

EDUCATIONAL_NOTE = "Insider activity is only one signal and should not be used alone."

EMPTY_MESSAGE = "No recent insider activity found"

# SEC Form 4 transaction codes (simplified for research UI)
_BUY_CODES = frozenset({"P"})  # open-market / discretionary purchase
_SELL_CODES = frozenset({"S"})  # open-market sale
_ACQUIRE_CODES = frozenset({"A", "M", "G", "W"})
_OTHER_CODES = frozenset({"F", "D", "C", "E", "H", "I", "J", "K", "L", "O", "U", "V", "X", "Z"})


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f else None  # NaN check
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    f = _safe_float(v)
    if f is None:
        return None
    return int(round(f))


def _role_from_row(row: dict[str, Any]) -> str | None:
    for key in ("position", "title", "relationship", "officerTitle", "role", "reportingOwner"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:120]
    return None


def _side_from_row(row: dict[str, Any]) -> str:
    code = str(row.get("transactionCode") or "").strip().upper()
    change = _safe_float(row.get("change"))
    if code in _BUY_CODES:
        return "Buy"
    if code in _SELL_CODES:
        return "Sell"
    if change is not None:
        if change > 0 and code in _ACQUIRE_CODES:
            return "Other"
        if change > 0:
            return "Buy"
        if change < 0:
            return "Sell"
    if code in _ACQUIRE_CODES:
        return "Other"
    if code in _OTHER_CODES or code:
        return "Other"
    return "Other"


def _normalize_row(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    side = _side_from_row(row)
    change = _safe_float(row.get("change"))
    if change is not None and change != 0:
        shares = _safe_int(abs(change))
    else:
        shares = None
    price = _safe_float(row.get("transactionPrice"))
    value = None
    if shares is not None and price is not None and shares > 0 and price > 0:
        value = round(shares * price, 2)
    tx_date = row.get("transactionDate") or row.get("filingDate")
    tx_date_s = str(tx_date).strip()[:10] if tx_date else None
    filing = row.get("filingDate")
    filing_s = str(filing).strip()[:10] if filing else None
    return {
        "insider_name": name.strip(),
        "role_title": _role_from_row(row),
        "side": side,
        "shares": shares,
        "price_per_share": round(price, 4) if price is not None else None,
        "transaction_value_usd": value,
        "transaction_date": tx_date_s,
        "filing_date": filing_s,
        "sec_code": str(row.get("transactionCode") or "").strip().upper() or None,
    }


def _sort_key(item: dict[str, Any]) -> str:
    return str(item.get("transaction_date") or item.get("filing_date") or "")


def compute_insider_summary(items: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (tone, text) — cautious language, not overstated."""
    buy_vol = 0
    sell_vol = 0
    for it in items:
        side = str(it.get("side") or "")
        sh = it.get("shares")
        try:
            n = int(sh) if sh is not None else 0
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            continue
        if side == "Buy":
            buy_vol += n
        elif side == "Sell":
            sell_vol += n
    if buy_vol == 0 and sell_vol == 0:
        return "neutral", "Recent insider activity appears neutral"
    if buy_vol > sell_vol * 1.25:
        return "bullish", "Recent insider activity appears bullish"
    if sell_vol > buy_vol * 1.25:
        return "cautious", "Recent insider activity appears cautious"
    return "neutral", "Recent insider activity appears neutral"


def empty_insider_payload(
    symbol: str,
    provider: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    tone, summary = compute_insider_summary([])
    return {
        "symbol": symbol.upper().strip(),
        "provider": provider,
        "count": 0,
        "items": [],
        "summary_tone": tone,
        "summary_text": summary,
        "message": EMPTY_MESSAGE,
        "empty": True,
        "educational_note": EDUCATIONAL_NOTE,
        "feed_available": False,
        "reason": reason,
    }


def build_insider_activity_payload(
    symbol: str,
    provider: str,
    raw: dict[str, Any] | list[Any] | None,
    *,
    finnhub_available: bool = True,
    limit: int = 12,
) -> dict[str, Any]:
    sym = symbol.upper().strip()
    if raw is None:
        out = empty_insider_payload(sym, provider, reason="insider_feed_unavailable")
        out["feed_available"] = finnhub_available
        return out

    rows: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            rows = [x for x in data if isinstance(x, dict)]
    elif isinstance(raw, list):
        rows = [x for x in raw if isinstance(x, dict)]

    normalized: list[dict[str, Any]] = []
    for row in rows:
        item = _normalize_row(row)
        if item:
            normalized.append(item)

    normalized.sort(key=_sort_key, reverse=True)
    capped = normalized[: max(1, int(limit))]

    if not capped:
        out = empty_insider_payload(sym, provider)
        out["feed_available"] = True
        return out

    tone, summary = compute_insider_summary(capped)
    return {
        "symbol": sym,
        "provider": provider,
        "count": len(capped),
        "items": capped,
        "summary_tone": tone,
        "summary_text": summary,
        "message": None,
        "empty": False,
        "educational_note": EDUCATIONAL_NOTE,
        "feed_available": True,
        "reason": None,
    }
