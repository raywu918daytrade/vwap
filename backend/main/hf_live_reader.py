"""Refresh mutable intraday M1 snapshots from the shared HF Dataset."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_LAST_CHECK: dict[str, float] = {}
_LAST_MANIFEST_KEY: dict[str, str] = {}


def refresh_live_m1(date_str: str) -> tuple[bool, bool]:
    """Return ``(cache_available, downloaded_new_snapshot)`` for one date."""
    date_str = str(date_str)[:10]
    relative_path = f"db/m1_live/{date_str}.parquet"
    target = _ROOT / relative_path
    repo_id = os.environ.get("HF_REPO_ID", "").strip()
    if not repo_id:
        return target.exists(), False

    try:
        ttl = max(1.0, float(os.environ.get("HF_LIVE_REFRESH_SECONDS", "60")))
    except ValueError:
        ttl = 60.0
    now = time.monotonic()
    if now - _LAST_CHECK.get(relative_path, 0.0) < ttl:
        return target.exists(), False

    with _LOCK:
        now = time.monotonic()
        if now - _LAST_CHECK.get(relative_path, 0.0) < ttl:
            return target.exists(), False
        _LAST_CHECK[relative_path] = now
        try:
            from huggingface_hub import hf_hub_download

            download_args = {
                "repo_id": repo_id,
                "repo_type": "dataset",
                "token": os.environ.get("HF_TOKEN") or None,
                "force_download": True,
            }
            manifest_path = hf_hub_download(filename="manifest/live.json", **download_args)
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            if manifest.get("trading_date") != date_str:
                return target.exists(), False
            if manifest.get("files", {}).get("m1_live") != relative_path:
                raise ValueError("live manifest points to an unexpected snapshot")

            manifest_key = str(manifest.get("latest_minute") or manifest.get("generated_at") or "")
            if not manifest_key:
                raise ValueError("live manifest has no version key")
            if _LAST_MANIFEST_KEY.get(relative_path) == manifest_key and target.exists():
                return True, False

            downloaded = hf_hub_download(filename=relative_path, **download_args)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".parquet.tmp")
            shutil.copyfile(downloaded, temporary)
            os.replace(temporary, target)
            _LAST_MANIFEST_KEY[relative_path] = manifest_key
            return True, True
        except Exception as exc:
            print(f"[HF live] {relative_path} refresh failed: {exc}", flush=True)
            return target.exists(), False


def sync_live_m1(date_str: str) -> bool:
    """Ensure one daily live snapshot is cached locally."""
    available, _changed = refresh_live_m1(date_str)
    return available
