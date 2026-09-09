"""Download only the monthly chart shards requested by a runtime service."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from main.runtime_profile import uses_on_demand_hf

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=False)

_LOCKS = [threading.Lock() for _ in range(16)]
_CHECKED_PATHS: set[str] = set()
_CHECKED_LOCK = threading.Lock()
_CACHE_FOLDERS = ("m1", "d1", "adjustment_day", "adjustment_factor", "tick_adjust_factor")


def _month_stems(start: date, end: date) -> list[str]:
    stems: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        stems.append(f"{year:04d}_{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return stems


def _download(relative_path: str) -> bool:
    local_path = _ROOT / relative_path
    with _CHECKED_LOCK:
        if relative_path in _CHECKED_PATHS and local_path.exists():
            return True

    lock = _LOCKS[hash(relative_path) % len(_LOCKS)]
    with lock:
        with _CHECKED_LOCK:
            if relative_path in _CHECKED_PATHS and local_path.exists():
                return True

        repo_id = os.environ.get("HF_REPO_ID", "").strip()
        if not repo_id:
            print(f"[HF按需下載] 缺少 HF_REPO_ID，無法取得 {relative_path}", flush=True)
            return False

        try:
            from huggingface_hub import hf_hub_download

            hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                token=os.environ.get("HF_TOKEN") or None,
                filename=relative_path,
                local_dir=str(_ROOT),
            )
        except Exception as exc:
            print(f"[HF按需下載] {relative_path} 失敗: {exc}", flush=True)
            return False

        with _CHECKED_LOCK:
            _CHECKED_PATHS.add(relative_path)
        print(f"[HF按需下載] 已取得 {relative_path}", flush=True)
        return True


def _prune_month_cache() -> None:
    try:
        limit = max(1, int(os.environ.get("CHART_CACHE_MONTHS", "12")))
    except ValueError:
        limit = 12

    for name in _CACHE_FOLDERS:
        folder = _ROOT / "db" / name
        if not folder.exists():
            continue
        paths = sorted(folder.glob("????_??.parquet"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths[limit:]:
            relative_path = str(path.relative_to(_ROOT))
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            with _CHECKED_LOCK:
                _CHECKED_PATHS.discard(relative_path)


def prune_month_cache() -> None:
    """Apply the shared rolling monthly-file limit without downloading data."""
    if uses_on_demand_hf():
        _prune_month_cache()


def ensure_chart_data(
    timeframe: str,
    requested_date: str | None,
    limit: int = 120,
    full_day: bool = False,
) -> None:
    """Ensure the exact monthly parquet files needed by one chart request."""
    if not uses_on_demand_hf():
        return

    try:
        ref_date = datetime.strptime(str(requested_date)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        ref_date = date.today()

    targets: list[str] = []
    if timeframe == "day":
        lookback_days = max(180, int((limit or 120) * 1.8))
        stems = _month_stems(ref_date - timedelta(days=lookback_days), ref_date)
        for stem in stems:
            targets.extend(
                (
                    f"db/adjustment_day/{stem}.parquet",
                    f"db/tick_adjust_factor/{stem}.parquet",
                )
            )
    elif timeframe == "1m":
        start_date = ref_date if full_day else ref_date - timedelta(days=45)
        stems = _month_stems(start_date, ref_date)
        for stem in stems:
            targets.extend(
                (
                    f"db/m1/{stem}.parquet",
                    f"db/adjustment_factor/{stem}.parquet",
                )
            )
    else:
        return

    try:
        configured_workers = max(1, int(os.environ.get("HF_DOWNLOAD_WORKERS", "4")))
    except ValueError:
        configured_workers = 4
    workers = min(configured_workers, len(targets))
    if workers:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_download, targets))
    _prune_month_cache()


def invalidate_current_month() -> None:
    """Force mutable current-month chart shards to refresh after daily HF sync."""
    if not uses_on_demand_hf():
        return
    stem = date.today().strftime("%Y_%m")
    for folder in _CACHE_FOLDERS:
        path = _ROOT / "db" / folder / f"{stem}.parquet"
        relative_path = str(path.relative_to(_ROOT))
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        with _CHECKED_LOCK:
            _CHECKED_PATHS.discard(relative_path)
