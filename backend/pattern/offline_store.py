"""Read and write precomputed D1 pattern scan results.

Runtime services should treat D1 pattern scans as data downloaded from HF, not
as work to calculate on the Oracle VM. Files are monthly parquet shards:

    db/pattern_scan/d1/YYYY_MM.parquet
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

_ROOT = Path(__file__).parent.parent
PATTERN_SCAN_DIR = _ROOT / "db/pattern_scan/d1"
PATTERN_SCAN_COLUMNS = [
    "scan_date",
    "stock_id",
    "stock_name",
    "pattern_type",
    "pattern_name",
    "timeframe",
    "score",
    "event_date",
    "payload_json",
]
PATTERN_SCAN_SUMMARY_COLUMNS = [column for column in PATTERN_SCAN_COLUMNS if column != "payload_json"]


def _month_path(date_str: str) -> Path:
    return PATTERN_SCAN_DIR / f"{date_str[:7].replace('-', '_')}.parquet"


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _date_key(value: Any) -> str:
    return _clean_text(value)[:10]


def _payload_from_row(row: pd.Series) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    raw = row.get("payload_json")
    if isinstance(raw, str) and raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}

    payload["scan_date"] = _date_key(row.get("scan_date", payload.get("scan_date", "")))
    payload["stock_id"] = _clean_text(row.get("stock_id", payload.get("stock_id", "")))
    payload["stock_name"] = _clean_text(row.get("stock_name", payload.get("stock_name", "")))
    payload["name"] = payload["stock_name"]
    payload["pattern_type"] = _clean_text(row.get("pattern_type", payload.get("pattern_type", "")))
    payload["pattern_name"] = _clean_text(row.get("pattern_name", payload.get("pattern_name", "")))
    payload["timeframe"] = _clean_text(row.get("timeframe", payload.get("timeframe", "day"))) or "day"
    payload["score"] = float(row.get("score", payload.get("score", 0)) or 0)
    payload["event_date"] = _date_key(row.get("event_date", payload.get("event_date", "")))
    payload["in_tick_universe"] = True
    return payload


def _summary_from_row(row: pd.Series) -> dict[str, Any]:
    """Build the small row shape used by scan lists.

    Chart overlays use `read_pattern_for_stock()` to load `payload_json` only
    for the selected stock/pattern. Keeping scan lists summary-only avoids
    sending pivots/lines for every match on every date switch.
    """
    stock_name = _clean_text(row.get("stock_name", ""))
    return {
        "scan_date": _date_key(row.get("scan_date", "")),
        "stock_id": _clean_text(row.get("stock_id", "")),
        "stock_name": stock_name,
        "name": stock_name,
        "pattern_type": _clean_text(row.get("pattern_type", "")),
        "pattern_name": _clean_text(row.get("pattern_name", "")),
        "timeframe": _clean_text(row.get("timeframe", "day")) or "day",
        "score": float(row.get("score", 0) or 0),
        "event_date": _date_key(row.get("event_date", "")),
        "in_tick_universe": True,
    }


def available_scan_dates() -> list[str]:
    """Return all scan dates present in local monthly parquet shards."""
    try:
        from data.tidb_offline_store import DATASET_PATTERN_SCAN, tidb_available_dates

        tidb_dates = tidb_available_dates(DATASET_PATTERN_SCAN)
        if tidb_dates:
            return tidb_dates
    except Exception as exc:
        print(f"[TiDB] pattern date lookup failed; falling back to parquet: {exc}", flush=True)

    dates: set[str] = set()
    for path in sorted(PATTERN_SCAN_DIR.glob("*.parquet")):
        try:
            df = pd.read_parquet(path, columns=["scan_date"])
        except Exception:
            continue
        if df.empty:
            continue
        dates.update(str(value)[:10] for value in df["scan_date"].dropna().unique())
    return sorted(dates)


def read_pattern_scan(
    date: str | None,
    pattern_types: Iterable[str],
    min_score: float = 60.0,
    *,
    include_payload: bool = True,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Read precomputed pattern rows for one trading date.

    If `date` is omitted, the latest available scan date is used. Missing files
    intentionally return an empty result, so non-trading dates stay blank.
    """
    selected = {_clean_text(value) for value in pattern_types if _clean_text(value)}
    if not date:
        dates = available_scan_dates()
        date = dates[-1] if dates else None
    if not date:
        return None, []
    date = _date_key(date)

    try:
        from data.tidb_offline_store import tidb_read_pattern_scan

        tidb_rows = tidb_read_pattern_scan(
            date,
            pattern_types,
            min_score,
            include_payload=include_payload,
        )
        if tidb_rows is not None:
            return date, tidb_rows
    except Exception as exc:
        print(f"[TiDB] pattern scan read failed; falling back to parquet: {exc}", flush=True)

    path = _month_path(date)
    if not path.exists():
        return date, []

    columns = None if include_payload else PATTERN_SCAN_SUMMARY_COLUMNS
    try:
        df = pd.read_parquet(path, columns=columns)
    except Exception:
        return date, []
    if df.empty:
        return date, []

    df["scan_date"] = df["scan_date"].astype(str).str[:10]
    df = df[df["scan_date"] == date]
    if selected:
        df = df[df["pattern_type"].astype(str).isin(selected)]
    if min_score is not None:
        df = df[pd.to_numeric(df["score"], errors="coerce").fillna(0) >= float(min_score)]
    if df.empty:
        return date, []

    df = df.sort_values(["score", "stock_id", "pattern_type"], ascending=[False, True, True])
    row_factory = _payload_from_row if include_payload else _summary_from_row
    return date, [row_factory(row) for _, row in df.iterrows()]


def read_pattern_for_stock(
    stock_id: str,
    pattern_type: str,
    date: str | None,
    min_score: float = 60.0,
) -> dict[str, Any] | None:
    """Return one precomputed pattern payload for chart overlay."""
    if not date:
        dates = available_scan_dates()
        date = dates[-1] if dates else None
    if not date:
        return None
    date = _date_key(date)

    try:
        from data.tidb_offline_store import tidb_read_pattern_for_stock

        tidb_row = tidb_read_pattern_for_stock(stock_id, pattern_type, date, min_score)
        if tidb_row is not None:
            return tidb_row
    except Exception as exc:
        print(f"[TiDB] pattern detail read failed; falling back to parquet: {exc}", flush=True)

    path = _month_path(date)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty:
        return None

    sid = str(stock_id)
    df["scan_date"] = df["scan_date"].astype(str).str[:10]
    df = df[df["scan_date"] == date]
    df = df[df["pattern_type"].astype(str) == str(pattern_type)]
    df = df[df["stock_id"].astype(str) == sid]
    if min_score is not None:
        df = df[pd.to_numeric(df["score"], errors="coerce").fillna(0) >= float(min_score)]
    if df.empty:
        return None
    df = df.sort_values(["score"], ascending=[False])
    return _payload_from_row(df.iloc[0])


def write_pattern_scan(date: str, rows: list[dict[str, Any]]) -> Path:
    """Upsert one trading date's pattern rows into its monthly parquet shard."""
    PATTERN_SCAN_DIR.mkdir(parents=True, exist_ok=True)
    path = _month_path(date)
    records = []
    for row in rows:
        payload_json = json.dumps(row, ensure_ascii=False, default=_json_default)
        stock_name = _clean_text(row.get("stock_name") or row.get("name") or "")
        records.append(
            {
                "scan_date": date,
                "stock_id": _clean_text(row.get("stock_id", "")),
                "stock_name": stock_name,
                "pattern_type": _clean_text(row.get("pattern_type", "")),
                "pattern_name": _clean_text(row.get("pattern_name", "")),
                "timeframe": _clean_text(row.get("timeframe", "day")) or "day",
                "score": float(row.get("score", 0) or 0),
                "event_date": _date_key(row.get("event_date", "")),
                "payload_json": payload_json,
            }
        )
    df_new = pd.DataFrame.from_records(records, columns=PATTERN_SCAN_COLUMNS)

    if path.exists():
        try:
            df_old = pd.read_parquet(path)
        except Exception:
            df_old = pd.DataFrame(columns=PATTERN_SCAN_COLUMNS)
    else:
        df_old = pd.DataFrame(columns=PATTERN_SCAN_COLUMNS)

    if not df_old.empty:
        df_old["scan_date"] = df_old["scan_date"].astype(str).str[:10]
        df_old = df_old[df_old["scan_date"] != date]

    df_all = df_new if df_old.empty else pd.concat([df_old, df_new], ignore_index=True)
    if not df_all.empty:
        df_all = df_all.sort_values(
            ["scan_date", "stock_id", "pattern_type", "score"],
            ascending=[True, True, True, False],
        )
    df_all.to_parquet(path, index=False)
    return path
