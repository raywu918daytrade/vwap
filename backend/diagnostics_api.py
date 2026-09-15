"""Sanitized live diagnostics for the public dashboard."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter

router = APIRouter()

_TW = timezone(timedelta(hours=8))
_snapshot_lock = threading.Lock()
_minute_snapshot: dict = {}


def _scalar(value):
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def update_minute_snapshot(minute_str: str, frame: pd.DataFrame) -> None:
    """Capture non-sensitive M1 health metrics without rereading parquet."""
    snapshot = {
        "updated_at": datetime.now(_TW).isoformat(timespec="seconds"),
        "latest_minute": minute_str,
        "rows": 0,
        "stocks": 0,
        "minutes": 0,
        "first_minute": None,
        "latest_minute_stocks": 0,
        "latest_delay_seconds": None,
        "missing_market_minutes_count": 0,
        "missing_market_minutes": [],
        "quote_0050": None,
    }
    if frame is not None and not frame.empty and {"date", "stock_id"}.issubset(frame.columns):
        dates = frame["date"].astype(str).str[:19]
        valid = dates.str.startswith(minute_str[:10])
        current = frame.loc[valid].copy()
        dates = dates.loc[valid]
        if not current.empty:
            current["_minute"] = dates.str[:16] + ":00"
            observed = set(current["_minute"].dropna().astype(str))
            latest = max(observed, default=minute_str)
            latest_rows = current[current["_minute"] == latest]
            snapshot.update(
                {
                    "latest_minute": latest,
                    "rows": int(len(current)),
                    "stocks": int(current["stock_id"].astype(str).nunique()),
                    "minutes": len(observed),
                    "first_minute": min(observed, default=None),
                    "latest_minute_stocks": int(latest_rows["stock_id"].astype(str).nunique()),
                }
            )
            try:
                latest_dt = datetime.strptime(latest, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TW)
                snapshot["latest_delay_seconds"] = max(0, int((datetime.now(_TW) - latest_dt).total_seconds()))
            except ValueError:
                pass

            start = datetime.strptime(f"{minute_str[:10]} 09:00:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TW)
            close = start.replace(hour=13, minute=29)
            target = datetime.strptime(minute_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_TW)
            end = min(target, close)
            if end >= start:
                expected = {
                    item.strftime("%Y-%m-%d %H:%M:00")
                    for item in pd.date_range(start, end, freq="min")
                }
                missing = sorted(expected - observed)
                snapshot["missing_market_minutes_count"] = len(missing)
                snapshot["missing_market_minutes"] = missing[-10:]

            quote_rows = current[current["stock_id"].astype(str) == "0050"]
            if not quote_rows.empty:
                row = quote_rows.sort_values("_minute").iloc[-1]
                snapshot["quote_0050"] = {
                    key: _scalar(row.get(key))
                    for key in ("_minute", "open", "high", "low", "close", "volume")
                }

    with _snapshot_lock:
        _minute_snapshot.clear()
        _minute_snapshot.update(snapshot)


def _safe_event(kind: str, row: dict) -> dict:
    return {
        "kind": kind,
        "time": str(row.get("time") or ""),
        "stock_id": str(row.get("stock_id") or ""),
        "name": str(row.get("name") or "")[:40],
        "direction": str(row.get("direction") or row.get("vwap_dir") or ""),
        "sr_kind": str(row.get("sr_kind") or ""),
        "price": _scalar(row.get("price")),
        "vwap": _scalar(row.get("vwap")),
    }


@router.get("/api/diagnostics/live", tags=["系統"], summary="安全的盤中診斷摘要")
def live_diagnostics() -> dict:
    # Import lazily because api.py installs this router while it is initializing.
    import api

    now = datetime.now(_TW)
    with _snapshot_lock:
        m1 = dict(_minute_snapshot)
    if not m1:
        # After a deploy/restart there may be no new minute callback yet. Load the
        # current day's parquet once so the panel remains useful after hours.
        try:
            from data.query import load_m1_live

            frame = load_m1_live(now.strftime("%Y-%m-%d"))
            if frame is not None and not frame.empty and "date" in frame.columns:
                latest = pd.to_datetime(frame["date"], errors="coerce").max()
                if not pd.isna(latest):
                    update_minute_snapshot(latest.strftime("%Y-%m-%d %H:%M:00"), frame)
                    with _snapshot_lock:
                        m1 = dict(_minute_snapshot)
        except Exception:
            # Diagnostics must never make the trading API unavailable.
            pass
    with api._lock:
        vwap_rows = list(api._vwap_breakout_signals)
        sr_rows = list(api._sr_vwap_cross_signals)
        coverage = dict(api._collector_coverage)
        safe_logs = [
            {"time": row.get("time"), "level": row.get("level"), "msg": row.get("msg")}
            for row in api._system_logs
            if str(row.get("msg") or "").startswith(("富邦 WebSocket 訂閱完成", "富邦 backfill 完成"))
        ][-10:]
        error_count = sum(1 for row in api._system_logs if row.get("level") == "error")

    events = [_safe_event("VWAP", row) for row in vwap_rows]
    events.extend(_safe_event("SR", row) for row in sr_rows)
    events.sort(key=lambda row: (row["time"], row["stock_id"], row["kind"]))

    delay = m1.get("latest_delay_seconds")
    market_minutes = now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (13, 32)
    freshness_ok = bool(m1.get("latest_minute")) and (not market_minutes or (delay is not None and delay <= 180))
    ok = api._data_ready and api._collector_status != "error" and freshness_ok

    return {
        "ok": ok,
        "generated_at": now.isoformat(timespec="seconds"),
        "health": {
            "status": "ok" if api._data_ready else "starting",
            "collector": api._collector_status,
            "message": api._COLLECTOR_MSG.get(api._collector_status, api._collector_status),
            "sse_clients": len(api._sse_clients),
            "coverage": coverage,
            "version": api._APP_VERSION,
        },
        "m1": {**m1, "freshness_ok": freshness_ok},
        "signals": {
            "vwap_count": len(vwap_rows),
            "sr_count": len(sr_rows),
            "latest_time": max((row["time"] for row in events), default=None),
            "latest_events": events[-20:],
        },
        "operations": {"safe_logs": safe_logs, "error_count": error_count},
    }
