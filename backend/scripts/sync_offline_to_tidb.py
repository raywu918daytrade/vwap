"""Sync local offline dashboard parquet shards into TiDB."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.tidb_offline_store import (  # noqa: E402
    DATASET_PATTERN_SCAN,
    DATASET_VWAP_ACTIVITY,
    DATASET_VWAP_SIGNALS,
    sync_offline_paths_to_tidb,
)

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=True)

_DATASET_DIRS = {
    DATASET_PATTERN_SCAN: _ROOT / "db/pattern_scan/d1",
    DATASET_VWAP_ACTIVITY: _ROOT / "db/vwap_activity",
    DATASET_VWAP_SIGNALS: _ROOT / "db/vwap_signals",
}


def _date_key(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)[:10]


def _filter_dates(values: Iterable[str], from_date: str | None, to_date: str | None) -> list[str]:
    out = []
    for value in values:
        date = _date_key(value)
        if not date:
            continue
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        out.append(date)
    return sorted(set(out))


def _month_stems_for_dates(dates: Iterable[str]) -> set[str]:
    return {date[:7].replace("-", "_") for date in dates if date}


def _paths_for_datasets(datasets: Iterable[str], dates: list[str]) -> list[Path]:
    stems = _month_stems_for_dates(dates)
    paths = []
    for dataset in datasets:
        folder = _DATASET_DIRS[dataset]
        if stems:
            paths.extend(folder / f"{stem}.parquet" for stem in sorted(stems))
        else:
            paths.extend(sorted(folder.glob("*.parquet")))
    return [path for path in paths if path.exists()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync offline dashboard parquet shards into TiDB")
    parser.add_argument("paths", nargs="*", help="Optional explicit parquet shard paths")
    parser.add_argument(
        "--dataset",
        action="append",
        choices=sorted(_DATASET_DIRS),
        help="Dataset to sync when paths are omitted. Can be repeated; defaults to all.",
    )
    parser.add_argument("--date", default=None, help="Only replace one scan_date YYYY-MM-DD")
    parser.add_argument("--from-date", default=None, help="Only replace dates on or after YYYY-MM-DD")
    parser.add_argument("--to-date", default=None, help="Only replace dates on or before YYYY-MM-DD")
    args = parser.parse_args()

    dates = _filter_dates([args.date], args.from_date, args.to_date) if args.date else []
    if args.from_date or args.to_date:
        # Date ranges need the parquet rows to tell us which trading dates exist.
        dates = []

    if args.paths:
        paths = [Path(path) for path in args.paths]
    else:
        datasets = args.dataset or sorted(_DATASET_DIRS)
        if args.date:
            paths = _paths_for_datasets(datasets, dates)
        else:
            paths = _paths_for_datasets(datasets, [])

    if args.from_date or args.to_date:
        date_values: set[str] = set()
        for path in paths:
            try:
                df = pd.read_parquet(path, columns=["scan_date"])
            except Exception:
                continue
            date_values.update(_date_key(value) for value in df["scan_date"].dropna().unique())
        dates = _filter_dates(date_values, args.from_date, args.to_date)

    summary = sync_offline_paths_to_tidb(paths, dates=dates or None)
    if summary:
        print(f"TiDB sync summary: {summary}", flush=True)
    else:
        print("TiDB sync completed with no inserted rows", flush=True)


if __name__ == "__main__":
    main()
