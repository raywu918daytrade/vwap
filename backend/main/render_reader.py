"""Lightweight Render entry point for HF-backed historical queries."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import uvicorn

from api import clear_vwap_bundle_cache, get_uvicorn_config, set_data_ready
from main import startup_data
from main.config import HF_DAILY_SYNC_HOUR, HF_DAILY_SYNC_MIN

_TW = timezone(timedelta(hours=8))
_startup_done = threading.Event()
_startup_attempt_at: datetime | None = None


def _refresh_reader_data() -> bool:
    synced = startup_data.sync_runtime_query_data_from_hf()
    if synced:
        startup_data.clear_market_query_caches()
        clear_vwap_bundle_cache()
    return synced


def _startup() -> None:
    global _startup_attempt_at
    try:
        print("[Render reader] 同步離線結果與股票清單...", flush=True)
        _refresh_reader_data()
        startup_data.warm_tidb_query_connection()
    finally:
        _startup_attempt_at = datetime.now(_TW)
        set_data_ready(True)
        _startup_done.set()
        print("[Render reader] 歷史查詢服務就緒", flush=True)


def _daily_sync() -> None:
    last_sync_date = None
    while not _startup_done.wait(timeout=1):
        pass
    next_retry_at = (_startup_attempt_at or datetime.now(_TW)) + timedelta(minutes=30)
    while True:
        now = datetime.now(_TW)
        retry_ready = next_retry_at is None or now >= next_retry_at
        should_sync = (
            last_sync_date != now.date()
            and (now.hour, now.minute) >= (HF_DAILY_SYNC_HOUR, HF_DAILY_SYNC_MIN)
            and retry_ready
        )
        if should_sync:
            expected_date = startup_data.latest_market_db_check_date(now)
            if _refresh_reader_data() and startup_data.offline_signal_date_available(expected_date):
                from main.hf_on_demand import invalidate_current_month

                invalidate_current_month()
                last_sync_date = now.date()
                next_retry_at = None
                print(f"[Render reader] {expected_date} 離線資料同步完成", flush=True)
            else:
                next_retry_at = now + timedelta(minutes=30)
                print(f"[Render reader] 尚未取得 {expected_date}，30 分鐘後重試", flush=True)
        time.sleep(60)


def _server_port() -> int:
    try:
        return int(os.environ.get("PORT", "8000"))
    except ValueError as exc:
        raise RuntimeError("PORT must be an integer") from exc


if __name__ == "__main__":
    set_data_ready(False)
    threading.Thread(target=_startup, daemon=True).start()
    threading.Thread(target=_daily_sync, daemon=True).start()
    port = _server_port()
    print(f"Render reader listening on 0.0.0.0:{port}", flush=True)
    uvicorn.Server(get_uvicorn_config(host="0.0.0.0", port=port)).run()
