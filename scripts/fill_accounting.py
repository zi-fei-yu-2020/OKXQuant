"""Conservative fee allocation from account-scoped archived exchange fills.

No network, model or order writes. A complete balanced fill volume and an exact
position-receipt fee total are required. This does not claim lifetime archive
completeness; funding/liquidation/settlement PnL stay in the authoritative receipt.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import hashlib
import math
from pathlib import Path
import sqlite3

FEE_TOLERANCE = Decimal("0.00000001")  # USDT comparison tolerance, not a fee guess.
MAX_ARCHIVED_FILLS = 50_000


def number(value, *, positive=False, integer=False):
    if value is None or isinstance(value, bool) or not str(value).strip():
        raise ValueError("missing_numeric_evidence")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid_numeric_evidence") from None
    if (not result.is_finite() or not math.isfinite(float(result))
            or (positive and result <= 0) or (integer and result != result.to_integral_value())):
        raise ValueError("invalid_numeric_evidence")
    return result


def interval(history):
    start = number(history.get("cTime"), positive=True, integer=True)
    end = number(history.get("uTime"), positive=True, integer=True)
    if end < start:
        raise ValueError("invalid_lifecycle_interval")
    return start, end


def lifecycle_key(history):
    return tuple(str(history.get(k, "")) for k in ("instId", "direction", "posId", "cTime", "uTime"))


@dataclass(frozen=True)
class FillArchive:
    status: str
    by_instrument: dict


def read_archive(scope, path=None):
    """Read only; missing/corrupt/oversized evidence never creates a new DB."""
    if path is None:
        from scripts.strategy_evidence import DB_PATH
        path = DB_PATH
    path = Path(path)
    if not path.is_file():
        return FillArchive("missing", {})
    if not isinstance(scope, str) or not scope:
        return FillArchive("invalid_scope", {})
    try:
        db = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=2)
        try:
            rows = db.execute("SELECT payload,digest FROM events WHERE scope=? AND kind='fill' LIMIT ?",
                              (scope, MAX_ARCHIVED_FILLS+1)).fetchall()
        finally:
            db.close()
        if len(rows) > MAX_ARCHIVED_FILLS:
            return FillArchive("capacity_exceeded", {})
        grouped = defaultdict(list)
        for raw, digest in rows:
            if not isinstance(raw, str) or hashlib.sha256(raw.encode()).hexdigest() != digest:
                return FillArchive("invalid_digest", {})
            row = json.loads(raw)
            if not isinstance(row, dict) or not isinstance(row.get("instId"), str):
                return FillArchive("invalid", {})
            grouped[row["instId"]].append(row)
        return FillArchive("available", dict(grouped))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return FillArchive("unavailable", {})


def reconcile(history, archive, *, peers=(), active_positions=()):
    """Return null allocation unless this receipt is supported by all checks.

    Use account bill ts (not a display-rounded time). Adjacent/overlapping
    lifecycle windows are ambiguous because fills do not carry a position ID.
    Directional hedge-mode USDT swaps only; net-mode flips are not inferred.
    """
    def unknown(reason, count=0):
        return {"open_fee": None, "close_fee": None,
                "fee_allocation": "unknown_until_fill_reconciliation",
                "fee_reconciliation": {"status": "unverified", "reason": reason, "matched_fills": count}}
    if archive.status != "available":
        return unknown("archive_"+archive.status)
    try:
        inst, direction = history.get("instId"), history.get("direction")
        if not isinstance(inst, str) or not inst.endswith("-USDT-SWAP") or direction not in ("long", "short"):
            return unknown("unsupported_instrument_or_position_mode")
        start, end = interval(history)
        target = number(history.get("closeTotalPos"), positive=True)
        receipt_fee = number(history.get("fee"))
        if history.get("ccy") not in (None, "", "USDT"):
            return unknown("unsupported_receipt_currency")
        siblings = []
        for other in peers:
            if other.get("instId") != inst or other.get("direction") != direction or lifecycle_key(other) == lifecycle_key(history):
                continue
            # Same opening event and position ID can be an updated receipt;
            # without exact lifecycle linkage its fill allocation is ambiguous.
            lo, hi = interval(other)
            if max(start, lo) <= min(end, hi):
                siblings.append((lo, hi))
        for position in active_positions:
            if position.get("instId") == inst and position.get("posSide") in (direction, "net") and number(position.get("pos")) != 0:
                if number(position.get("cTime"), positive=True) <= end:
                    return unknown("overlapping_active_position")
        matched = {}
        for fill in archive.by_instrument.get(inst, ()):
            if fill.get("posSide") not in (direction, "net", None, ""):
                continue
            ts = number(fill.get("ts"), positive=True, integer=True)
            if not start <= ts <= end:
                continue
            if any(lo <= ts <= hi for lo, hi in siblings):
                return unknown("ambiguous_lifecycle_overlap")
            if fill.get("posSide") != direction:
                return unknown("unsupported_fill_position_mode")
            identity = fill.get("billId")
            if not isinstance(identity, str) or not identity or not fill.get("ordId"):
                return unknown("missing_fill_identity")
            if identity in matched and matched[identity] != fill:
                return unknown("conflicting_fill_identity")
            matched[identity] = fill
        if not matched:
            return unknown("no_lifecycle_fills")
        opening_side = "buy" if direction == "long" else "sell"
        open_size = close_size = open_fee = close_fee = Decimal(0)
        # Position accounting must never go negative within a closed lifecycle.
        # Same-millisecond fills are grouped: exchange bill IDs are not clocks.
        movements = defaultdict(lambda: Decimal(0))
        for fill in matched.values():
            if fill.get("feeCcy") != "USDT":
                return unknown("unsupported_or_missing_fee_currency", len(matched))
            if fill.get("side") not in ("buy", "sell"):
                return unknown("invalid_fill_side", len(matched))
            size = number(fill.get("fillSz"), positive=True)
            fee = number(fill.get("fee"))
            ts = number(fill.get("ts"), positive=True, integer=True)
            if fill["side"] == opening_side:
                open_size += size; open_fee += fee; movements[ts] += size
            else:
                close_size += size; close_fee += fee; movements[ts] -= size
        running = Decimal(0)
        for ts in sorted(movements):
            running += movements[ts]
            if running < 0:
                return unknown("missing_opening_inventory", len(matched))
        if open_size != target or close_size != target or running != 0:
            return unknown("incomplete_or_excess_lifecycle_volume", len(matched))
        if abs(open_fee+close_fee-receipt_fee) > FEE_TOLERANCE:
            return unknown("fee_total_mismatch", len(matched))
        return {"open_fee": float(open_fee), "close_fee": float(close_fee),
                "fee_allocation": "verified_from_archived_fills",
                "fee_reconciliation": {"status": "verified", "currency": "USDT",
                    "basis": "account_scoped_fills_and_position_receipt",
                    "matched_fills": len(matched), "opening_size": str(open_size),
                    "closing_size": str(close_size), "receipt_fee": str(receipt_fee),
                    "allocated_fee": str(open_fee+close_fee), "tolerance": str(FEE_TOLERANCE),
                    "bill_ids": sorted(matched),
                    "opening_order_ids": sorted({str(f["ordId"]) for f in matched.values() if f["side"]==opening_side})}}
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return unknown("invalid_accounting_evidence")
