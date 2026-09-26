"""Sanitized Oracle -> HF -> Render market-data monitoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter

router = APIRouter()
_TW = timezone(timedelta(hours=8))


@router.get("/api/diagnostics/live", tags=["系統"], summary="報價資料流監控")
def live_diagnostics() -> dict:
    # Import lazily because api.py installs this router while it is initializing.
    import api
    from main.hf_live_reader import live_reader_status, refresh_live_m1

    now = datetime.now(_TW)
    reader = live_reader_status()
    if not reader.get("manifest"):
        refresh_live_m1(now.strftime("%Y-%m-%d"))
        reader = live_reader_status()

    manifest = reader.get("manifest") or {}
    coverage = manifest.get("coverage") or {}
    latest_minute = manifest.get("latest_minute")
    delay = None
    if latest_minute:
        try:
            latest_dt = datetime.fromisoformat(str(latest_minute)).replace(tzinfo=_TW)
            delay = max(0, int((now - latest_dt).total_seconds()))
        except ValueError:
            pass

    market_open = now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) <= (13, 35)
    same_day = manifest.get("trading_date") == now.strftime("%Y-%m-%d")
    fresh = bool(latest_minute) and delay is not None and delay <= 180
    coverage_ok = int(coverage.get("arrived") or 0) > 0
    quote_ok = same_day and fresh and coverage_ok if market_open else True
    render_ok = api._data_ready and (not reader.get("error") or not market_open)
    phase = "盤中" if market_open else "非交易時段"
    reader_error = str(reader.get("error") or "").split(":", 1)[0] or None

    return {
        "ok": render_ok and quote_ok,
        "generated_at": now.isoformat(timespec="seconds"),
        "phase": phase,
        "pipeline": {
            "oracle": "正常發布" if quote_ok and market_open else ("等待下一交易時段" if not market_open else "發布延遲"),
            "hf": "最新" if fresh and same_day else ("盤後保存" if not market_open and latest_minute else ("尚無發布紀錄" if not market_open else "等待資料")),
            "render": "已套用" if render_ok and reader.get("apply_mode") else ("服務正常" if render_ok else "同步異常"),
        },
        "quote": {
            "trading_date": manifest.get("trading_date"),
            "latest_minute": latest_minute,
            "delay_seconds": delay,
            "fresh": fresh,
            "coverage": coverage,
            "delta": manifest.get("delta") or {},
            "published_at": manifest.get("generated_at"),
        },
        "consumer": {
            "checked_at": reader.get("checked_at"),
            "applied_at": reader.get("applied_at"),
            "apply_mode": reader.get("apply_mode"),
            "error": reader_error,
        },
        "render": {
            "status": "ok" if api._data_ready else "starting",
            "sse_clients": len(api._sse_clients),
            "version": api._APP_VERSION,
        },
    }
