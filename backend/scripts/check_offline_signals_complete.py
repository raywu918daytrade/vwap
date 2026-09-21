"""Exit successfully only when every offline dataset contains the target date."""

from __future__ import annotations

import argparse

from data.tidb_offline_store import (
    DATASET_PATTERN_SCAN,
    DATASET_VWAP_ACTIVITY,
    DATASET_VWAP_SIGNALS,
    tidb_available_dates,
)


DATASETS = (DATASET_PATTERN_SCAN, DATASET_VWAP_ACTIVITY, DATASET_VWAP_SIGNALS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    try:
        missing = [name for name in DATASETS if args.date not in (tidb_available_dates(name) or [])]
    except Exception as exc:
        print(f"TiDB completion check unavailable: {exc}")
        return 1
    if missing:
        print(f"{args.date} incomplete: {', '.join(missing)}")
        return 1
    print(f"{args.date} already complete in TiDB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
