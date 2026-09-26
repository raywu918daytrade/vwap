"""Refresh mutable intraday M1 snapshots from the shared HF Dataset."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_LAST_CHECK: dict[str, float] = {}


def sync_live_m1(date_str: str) -> bool:
    """Refresh one daily live snapshot, bounded by a short process-wide TTL."""
    repo_id = os.environ.get("HF_REPO_ID", "").strip()
    if not repo_id:
        return False

    try:
        ttl = max(1.0, float(os.environ.get("HF_LIVE_REFRESH_SECONDS", "10")))
    except ValueError:
        ttl = 10.0
    relative_path = f"db/m1_live/{str(date_str)[:10]}.parquet"
    now = time.monotonic()
    if now - _LAST_CHECK.get(relative_path, 0.0) < ttl:
        return (_ROOT / relative_path).exists()

    with _LOCK:
        now = time.monotonic()
        if now - _LAST_CHECK.get(relative_path, 0.0) < ttl:
            return (_ROOT / relative_path).exists()
        _LAST_CHECK[relative_path] = now
        try:
            from huggingface_hub import hf_hub_download

            downloaded = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                token=os.environ.get("HF_TOKEN") or None,
                filename=relative_path,
                force_download=True,
            )
            target = _ROOT / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".parquet.tmp")
            shutil.copyfile(downloaded, temporary)
            os.replace(temporary, target)
            return True
        except Exception as exc:
            print(f"[HF live] {relative_path} refresh failed: {exc}", flush=True)
            return (_ROOT / relative_path).exists()
