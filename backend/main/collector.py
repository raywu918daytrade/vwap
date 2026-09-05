"""Fubon M1 WebSocket collector runner with automatic retry."""

from __future__ import annotations

import os
import time

from api import append_system_log as _log_sys, set_collector_status

_COLLECTOR_RETRY_DELAY = int(os.environ.get("COLLECTOR_RETRY_DELAY", "10"))


def start_collector(on_minute, backfill_done=None) -> None:
    """Run the M1 collector forever and retry with a fresh instance on errors."""
    from fubon.marketdata_ws import FubonM1Collector

    attempt = 0
    while True:
        attempt += 1
        collector = FubonM1Collector(on_minute=on_minute, backfill_done=backfill_done)
        try:
            set_collector_status("running")
            collector.start()
        except Exception as exc:
            set_collector_status("error")
            msg = f"Collector 中斷（第{attempt}次）: {exc}"
            print(msg, flush=True)
            _log_sys(msg, "error")
            print(f"  {_COLLECTOR_RETRY_DELAY} 秒後自動重試...", flush=True)
            time.sleep(_COLLECTOR_RETRY_DELAY)
            continue
        else:
            set_collector_status("stopped")
            break
