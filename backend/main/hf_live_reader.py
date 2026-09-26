"""Refresh mutable intraday M1 snapshots from the shared HF Dataset."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
_LOCK = threading.Lock()
_LAST_CHECK: dict[str, float] = {}
_LAST_MANIFEST_KEY: dict[str, str] = {}
_STATUS: dict[str, object] = {
    "checked_at": None,
    "applied_at": None,
    "apply_mode": None,
    "manifest": None,
    "error": None,
}


def live_reader_status() -> dict:
    with _LOCK:
        return dict(_STATUS)


def _is_next_minute(previous: str | None, current: str) -> bool:
    if not previous:
        return False
    try:
        return (datetime.fromisoformat(current) - datetime.fromisoformat(previous)).total_seconds() == 60
    except ValueError:
        return False


def _replace_snapshot(source: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".parquet.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, target)


def _replace_json(source: str, target: Path) -> None:
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("live signal snapshot must be an object")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, target)


def read_live_signals(date_str: str) -> dict | None:
    path = _ROOT / f"db/signal_live/{str(date_str)[:10]}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("trading_date") != str(date_str)[:10]:
        return None
    return payload


def _merge_delta(source: str, target: Path) -> None:
    current = pd.read_parquet(target)
    delta = pd.read_parquet(source)
    merged = pd.concat([current, delta], ignore_index=True)
    merged["stock_id"] = merged["stock_id"].astype(str)
    merged["date"] = pd.to_datetime(merged["date"], format="mixed").dt.strftime("%Y-%m-%d %H:%M:%S")
    merged.sort_values(["date", "stock_id"], inplace=True)
    merged.drop_duplicates(["stock_id", "date"], keep="last", inplace=True)
    temporary = target.with_suffix(".parquet.tmp")
    try:
        merged.to_parquet(temporary, index=False, compression="zstd")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


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
            _STATUS.update(
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                manifest=manifest,
                error=None,
            )
            if manifest.get("trading_date") != date_str:
                return target.exists(), False
            if manifest.get("files", {}).get("m1_live") != relative_path:
                raise ValueError("live manifest points to an unexpected snapshot")

            manifest_key = str(manifest.get("latest_minute") or manifest.get("generated_at") or "")
            if not manifest_key:
                raise ValueError("live manifest has no version key")
            if _LAST_MANIFEST_KEY.get(relative_path) == manifest_key and target.exists():
                _STATUS["apply_mode"] = "current"
                return True, False

            previous_key = _LAST_MANIFEST_KEY.get(relative_path)
            delta_path = manifest.get("files", {}).get("m1_delta")
            use_delta = (
                target.exists()
                and isinstance(delta_path, str)
                and delta_path.startswith(f"db/m1_delta/{date_str}/")
                and _is_next_minute(previous_key, manifest_key)
            )
            if use_delta:
                try:
                    downloaded = hf_hub_download(filename=delta_path, **download_args)
                    _merge_delta(downloaded, target)
                    apply_mode = "delta"
                except Exception as exc:
                    print(f"[HF live] delta failed, using full snapshot: {type(exc).__name__}: {exc}", flush=True)
                    downloaded = hf_hub_download(filename=relative_path, **download_args)
                    _replace_snapshot(downloaded, target)
                    apply_mode = "snapshot"
            else:
                downloaded = hf_hub_download(filename=relative_path, **download_args)
                _replace_snapshot(downloaded, target)
                apply_mode = "snapshot"
            signal_path = manifest.get("files", {}).get("signal_live")
            if isinstance(signal_path, str) and signal_path == f"db/signal_live/{date_str}.json":
                try:
                    signal_source = hf_hub_download(filename=signal_path, **download_args)
                    signal_target = _ROOT / signal_path
                    _replace_json(signal_source, signal_target)
                    signal_payload = read_live_signals(date_str)
                    if not signal_payload or signal_payload.get("latest_minute") != manifest_key:
                        raise ValueError("live signal minute does not match manifest")
                except Exception as exc:
                    print(f"[HF live] signal snapshot failed; keeping prior snapshot: {type(exc).__name__}: {exc}", flush=True)
            _LAST_MANIFEST_KEY[relative_path] = manifest_key
            _STATUS.update(
                applied_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                apply_mode=apply_mode,
                error=None,
            )
            return True, True
        except Exception as exc:
            _STATUS.update(
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                error=f"{type(exc).__name__}: {exc}",
            )
            print(f"[HF live] {relative_path} refresh failed: {exc}", flush=True)
            return target.exists(), False


def sync_live_m1(date_str: str) -> bool:
    """Ensure one daily live snapshot is cached locally."""
    available, _changed = refresh_live_m1(date_str)
    return available
