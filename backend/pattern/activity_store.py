"""Read and write precomputed VWAP activity metrics.

Runtime services should prefer these HF-synced parquet shards instead of
calculating 09:05 volume PR from historical M5 data on a small VM:

    db/vwap_activity/YYYY_MM.parquet
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as ds

_ROOT = Path(__file__).parent.parent
VWAP_ACTIVITY_DIR = _ROOT / "db/vwap_activity"
VWAP_ACTIVITY_COLUMNS = [
    "scan_date",
    "stock_id",
    "day_atr",
    "open5_rng",
    "vol5_pr",
]
_dates_cache: list[str] | None = None
_dates_lock = threading.Lock()


def _month_path(date_str: str) -> Path:
    return VWAP_ACTIVITY_DIR / f"{date_str[:7].replace('-', '_')}.parquet"


def _date_key(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)[:10]


def _num_or_none(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def has_activity_store() -> bool:
    """Return whether any local offline activity shard is available."""
    return VWAP_ACTIVITY_DIR.exists() and any(VWAP_ACTIVITY_DIR.glob("*.parquet"))


def available_activity_dates() -> list[str]:
    """Return all dates present in local VWAP activity parquet shards."""
    global _dates_cache
    with _dates_lock:
        if _dates_cache is not None:
            return list(_dates_cache)

    dates: set[str] = set()
    for path in sorted(VWAP_ACTIVITY_DIR.glob("*.parquet")):
        try:
            df = pd.read_parquet(path, columns=["scan_date"])
        except Exception:
            continue
        if df.empty:
            continue
        dates.update(_date_key(value) for value in df["scan_date"].dropna().unique())
    result = sorted(date for date in dates if date)
    with _dates_lock:
        _dates_cache = result
    return list(result)


def clear_cache() -> None:
    """Clear the date index after local HF shards are replaced."""
    global _dates_cache
    with _dates_lock:
        _dates_cache = None


def read_vwap_activity(
    date: str,
    stock_ids: set[str] | None = None,
) -> dict[str, dict] | None:
    """Read precomputed metrics for one date.

    Returns:
        None when the monthly shard is missing, so callers can decide whether
        to fall back to runtime calculation. An existing shard with no matching
        rows returns an empty dict; that represents a stable non-trading or
        not-yet-produced date inside an offline month.
    """
    date = _date_key(date)
    if not date:
        return {}
    path = _month_path(date)
    if not path.exists():
        return None

    try:
        table = ds.dataset(str(path), format="parquet").to_table(
            filter=ds.field("scan_date") == date,
            columns=VWAP_ACTIVITY_COLUMNS,
        )
    except Exception:
        try:
            df = pd.read_parquet(path, columns=VWAP_ACTIVITY_COLUMNS)
        except Exception:
            return {}
    else:
        if table.num_rows == 0:
            return {}
        df = table.to_pandas()

    if df.empty:
        return {}
    df["scan_date"] = df["scan_date"].astype(str).str[:10]
    df = df[df["scan_date"] == date]
    if stock_ids:
        df = df[df["stock_id"].astype(str).isin(stock_ids)]
    if df.empty:
        return {}

    out: dict[str, dict] = {}
    for row in df.itertuples(index=False):
        sid = str(row.stock_id)
        out[sid] = {
            "day_atr": _num_or_none(row.day_atr),
            "open5_rng": _num_or_none(row.open5_rng),
            "vol5_pr": _num_or_none(row.vol5_pr),
        }
    return out


def write_vwap_activity(date: str, metrics: dict[str, dict]) -> Path:
    """Upsert one trading date's precomputed metrics into a monthly shard."""
    date = _date_key(date)
    if not date:
        raise RuntimeError("date is required")

    VWAP_ACTIVITY_DIR.mkdir(parents=True, exist_ok=True)
    path = _month_path(date)
    records = []
    for stock_id, row in sorted(metrics.items()):
        records.append(
            {
                "scan_date": date,
                "stock_id": str(stock_id),
                "day_atr": _num_or_none(row.get("day_atr")),
                "open5_rng": _num_or_none(row.get("open5_rng")),
                "vol5_pr": _num_or_none(row.get("vol5_pr")),
            }
        )
    df_new = pd.DataFrame.from_records(records, columns=VWAP_ACTIVITY_COLUMNS)

    if path.exists():
        try:
            df_old = pd.read_parquet(path, columns=VWAP_ACTIVITY_COLUMNS)
        except Exception:
            df_old = pd.DataFrame(columns=VWAP_ACTIVITY_COLUMNS)
    else:
        df_old = pd.DataFrame(columns=VWAP_ACTIVITY_COLUMNS)

    if not df_old.empty:
        df_old["scan_date"] = df_old["scan_date"].astype(str).str[:10]
        df_old = df_old[df_old["scan_date"] != date]

    df_all = df_new if df_old.empty else pd.concat([df_old, df_new], ignore_index=True)
    if not df_all.empty:
        df_all = df_all.sort_values(["scan_date", "stock_id"])
    df_all.to_parquet(path, index=False)
    clear_cache()
    return path
