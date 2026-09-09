"""Fubon M1 WebSocket collector runner with automatic retry."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

from api import append_system_log as _log_sys, set_collector_status

_COLLECTOR_RETRY_DELAY = int(os.environ.get("COLLECTOR_RETRY_DELAY", "10"))
_TW = timezone(timedelta(hours=8))
_START_TIME = (int(os.environ.get("COLLECTOR_START_HOUR", "8")), int(os.environ.get("COLLECTOR_START_MIN", "0")))
_STOP_TIME = (int(os.environ.get("COLLECTOR_STOP_HOUR", "13")), int(os.environ.get("COLLECTOR_STOP_MIN", "30")))


def _within_collection_window(now: datetime) -> bool:
    return now.weekday() < 5 and _START_TIME <= (now.hour, now.minute) < _STOP_TIME


def within_collection_window(now: datetime | None = None) -> bool:
    """Return whether the configured Fubon collection session is active."""
    return _within_collection_window(now or datetime.now(_TW))


def start_collector(on_minute, backfill_done=None) -> None:
    """Run the M1 collector forever and retry with a fresh instance on errors."""
    from fubon import fubon_api
    from fubon.marketdata_ws import FubonM1Collector

    missing = fubon_api.missing_login_env()
    if missing:
        set_collector_status("error")
        msg = f"Collector 未啟動：缺少富邦登入環境變數 {', '.join(missing)}"
        print(msg, flush=True)
        _log_sys(msg, "error")
        return

    attempt = 0
    while True:
        now = datetime.now(_TW)
        if not _within_collection_window(now):
            set_collector_status("stopped")
            time.sleep(30)
            continue

        attempt += 1
        collector = FubonM1Collector(on_minute=on_minute, backfill_done=backfill_done)
        collector_finished = threading.Event()

        def stop_after_session() -> None:
            while not collector_finished.is_set() and _within_collection_window(datetime.now(_TW)):
                time.sleep(15)
            if not collector_finished.is_set():
                collector.stop()
                set_collector_status("stopped")
                print("富邦 WebSocket 已停止並登出", flush=True)

        threading.Thread(target=stop_after_session, daemon=True).start()
        try:
            set_collector_status("running")
            collector.start()
        except Exception as exc:
            collector.stop()
            collector_finished.set()
            set_collector_status("error")
            msg = f"Collector 中斷（第{attempt}次）: {exc}"
            print(msg, flush=True)
            _log_sys(msg, "error")
            print(f"  {_COLLECTOR_RETRY_DELAY} 秒後自動重試...", flush=True)
            time.sleep(_COLLECTOR_RETRY_DELAY)
            continue
        else:
            collector_finished.set()
            set_collector_status("stopped")
