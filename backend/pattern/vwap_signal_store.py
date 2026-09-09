"""Read and write precomputed intraday signal shards for the VWAP dashboard.

Historical API requests should read these HF-synced monthly parquet files
instead of recalculating whole-market M1 signals on the Oracle VM:

    db/vwap_signals/YYYY_MM.parquet

Today's live session is still calculated in memory by the realtime collector.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.dataset as ds

from main.runtime_profile import uses_on_demand_hf

_ROOT = Path(__file__).parent.parent
VWAP_SIGNAL_DIR = _ROOT / "db/vwap_signals"
VWAP_SIGNAL_COLUMNS = [
    "scan_date",
    "kind",
    "stock_id",
    "time",
    "value",
    "payload_json",
]
EVENT_KINDS = {"vwap", "sr"}
MAP_KINDS = {"macd", "obv"}
_CACHE_LIMIT = 0 if uses_on_demand_hf() else max(0, int(os.environ.get("VWAP_SIGNAL_CACHE_DATES", "80")))
_cache: dict[str, dict[str, Any]] = {}
_cache_order: list[str] = []
_dates_cache: list[str] | None = None
_lock = threading.Lock()


def _month_path(date_str: str) -> Path:
    return VWAP_SIGNAL_DIR / f"{date_str[:7].replace('-', '_')}.parquet"


def _date_key(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)[:10]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def _float_or_none(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _json_loads(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _empty_bundle() -> dict[str, Any]:
    return {
        "vwap": [],
        "sr": [],
        "macd": {},
        "obv": {},
        "chg": {},
        "sr_levels": {},
        "m1_bars": 0,
    }


def _copy_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "vwap": list(bundle.get("vwap", [])),
        "sr": list(bundle.get("sr", [])),
        "macd": dict(bundle.get("macd", {})),
        "obv": dict(bundle.get("obv", {})),
        "chg": dict(bundle.get("chg", {})),
        "sr_levels": dict(bundle.get("sr_levels", {})),
        "m1_bars": int(bundle.get("m1_bars") or 0),
    }


def _filter_bundle(bundle: dict[str, Any], stock_ids: set[str] | None) -> dict[str, Any]:
    if not stock_ids:
        return _copy_bundle(bundle)
    allowed = {str(stock_id) for stock_id in stock_ids}
    return {
        "vwap": [row for row in bundle.get("vwap", []) if str(row.get("stock_id")) in allowed],
        "sr": [row for row in bundle.get("sr", []) if str(row.get("stock_id")) in allowed],
        "macd": {sid: row for sid, row in bundle.get("macd", {}).items() if str(sid) in allowed},
        "obv": {sid: row for sid, row in bundle.get("obv", {}).items() if str(sid) in allowed},
        "chg": {sid: row for sid, row in bundle.get("chg", {}).items() if str(sid) in allowed},
        "sr_levels": {sid: row for sid, row in bundle.get("sr_levels", {}).items() if str(sid) in allowed},
        "m1_bars": int(bundle.get("m1_bars") or 0),
    }


def clear_cache() -> None:
    """Clear cached signal bundles after HF-sync overwrites parquet files."""
    global _dates_cache
    with _lock:
        _cache.clear()
        _cache_order.clear()
        _dates_cache = None


def _remember(date: str, bundle: dict[str, Any]) -> None:
    if _CACHE_LIMIT <= 0:
        return
    with _lock:
        _cache[date] = bundle
        if date in _cache_order:
            _cache_order.remove(date)
        _cache_order.append(date)
        while len(_cache_order) > _CACHE_LIMIT:
            oldest = _cache_order.pop(0)
            _cache.pop(oldest, None)


def has_vwap_signal_store() -> bool:
    """Return whether any local offline signal shard is available."""
    return VWAP_SIGNAL_DIR.exists() and any(VWAP_SIGNAL_DIR.glob("*.parquet"))


def available_signal_dates() -> list[str]:
    """Return dates present in precomputed intraday signal shards."""
    global _dates_cache
    with _lock:
        if _dates_cache is not None:
            return list(_dates_cache)

    dates: set[str] = set()
    for path in sorted(VWAP_SIGNAL_DIR.glob("*.parquet")):
        try:
            df = pd.read_parquet(path, columns=["scan_date"])
        except Exception:
            continue
        if df.empty:
            continue
        dates.update(_date_key(value) for value in df["scan_date"].dropna().unique())
    result = sorted(date for date in dates if date)
    with _lock:
        _dates_cache = result
    return list(result)


def read_vwap_signals(
    date: str,
    stock_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """Read one date's precomputed signal bundle.

    Returns:
        None when the monthly shard is missing. An existing shard with no rows
        returns an empty bundle, representing a stable non-trading or empty day.
    """
    date = _date_key(date)
    if not date:
        return _empty_bundle()
    with _lock:
        cached = _cache.get(date)
        if cached is not None:
            return _filter_bundle(cached, stock_ids)

    path = _month_path(date)
    if not path.exists():
        return None

    try:
        table = ds.dataset(str(path), format="parquet").to_table(
            filter=ds.field("scan_date") == date,
            columns=VWAP_SIGNAL_COLUMNS,
        )
    except Exception:
        try:
            df = pd.read_parquet(path, columns=VWAP_SIGNAL_COLUMNS)
        except Exception:
            return _empty_bundle()
    else:
        if table.num_rows == 0:
            return _empty_bundle()
        df = table.to_pandas()

    if df.empty:
        return _empty_bundle()

    df["scan_date"] = df["scan_date"].astype(str).str[:10]
    df = df[df["scan_date"] == date]
    if df.empty:
        return _empty_bundle()

    bundle = _empty_bundle()
    for row in df.itertuples(index=False):
        kind = _clean_text(row.kind)
        stock_id = _clean_text(row.stock_id)
        payload = _json_loads(row.payload_json)
        if kind in EVENT_KINDS:
            if not stock_id:
                continue
            payload["stock_id"] = _clean_text(payload.get("stock_id") or stock_id)
            payload["time"] = _clean_text(payload.get("time") or row.time)
            bundle[kind].append(payload)
        elif kind in MAP_KINDS:
            if stock_id:
                bundle[kind][stock_id] = payload
        elif kind == "chg":
            if stock_id:
                value = _float_or_none(row.value)
                if value is not None:
                    bundle["chg"][stock_id] = round(value, 2)
        elif kind == "sr_level":
            if stock_id:
                bundle["sr_levels"][stock_id] = {
                    "resistance": _float_or_none(payload.get("resistance")),
                    "support": _float_or_none(payload.get("support")),
                }
        elif kind == "meta":
            m1_bars = payload.get("m1_bars")
            try:
                bundle["m1_bars"] = int(m1_bars)
            except Exception:
                bundle["m1_bars"] = 0

    bundle["vwap"].sort(key=lambda item: (_clean_text(item.get("time")), _clean_text(item.get("stock_id"))), reverse=True)
    bundle["sr"].sort(key=lambda item: (_clean_text(item.get("time")), _clean_text(item.get("stock_id"))), reverse=True)
    _remember(date, bundle)
    return _filter_bundle(bundle, stock_ids)


def write_vwap_signals(
    date: str,
    *,
    vwap: list[dict],
    sr: list[dict],
    macd: dict[str, dict],
    obv: dict[str, dict],
    chg: dict[str, float],
    sr_levels: dict[str, tuple[float | None, float | None]] | None = None,
    m1_bars: int = 0,
) -> Path:
    """Upsert one date's full intraday signal bundle into its monthly shard."""
    date = _date_key(date)
    if not date:
        raise RuntimeError("date is required")

    records: list[dict[str, Any]] = [
        {
            "scan_date": date,
            "kind": "meta",
            "stock_id": "",
            "time": "",
            "value": None,
            "payload_json": json.dumps({"m1_bars": int(m1_bars or 0)}, ensure_ascii=False),
        }
    ]

    for kind, rows in (("vwap", vwap), ("sr", sr)):
        for item in rows or []:
            payload = dict(item)
            records.append(
                {
                    "scan_date": date,
                    "kind": kind,
                    "stock_id": _clean_text(payload.get("stock_id")),
                    "time": _clean_text(payload.get("time")),
                    "value": None,
                    "payload_json": json.dumps(payload, ensure_ascii=False, default=_json_default),
                }
            )

    for kind, values in (("macd", macd), ("obv", obv)):
        for stock_id, payload in sorted((values or {}).items()):
            records.append(
                {
                    "scan_date": date,
                    "kind": kind,
                    "stock_id": str(stock_id),
                    "time": "",
                    "value": None,
                    "payload_json": json.dumps(payload or {}, ensure_ascii=False, default=_json_default),
                }
            )

    for stock_id, value in sorted((chg or {}).items()):
        records.append(
            {
                "scan_date": date,
                "kind": "chg",
                "stock_id": str(stock_id),
                "time": "",
                "value": _float_or_none(value),
                "payload_json": "",
            }
        )

    for stock_id, levels in sorted((sr_levels or {}).items()):
        resistance, support = levels
        payload = {
            "resistance": _float_or_none(resistance),
            "support": _float_or_none(support),
        }
        records.append(
            {
                "scan_date": date,
                "kind": "sr_level",
                "stock_id": str(stock_id),
                "time": "",
                "value": None,
                "payload_json": json.dumps(payload, ensure_ascii=False, default=_json_default),
            }
        )

    VWAP_SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _month_path(date)
    df_new = pd.DataFrame.from_records(records, columns=VWAP_SIGNAL_COLUMNS)

    if path.exists():
        try:
            df_old = pd.read_parquet(path, columns=VWAP_SIGNAL_COLUMNS)
        except Exception:
            df_old = pd.DataFrame(columns=VWAP_SIGNAL_COLUMNS)
    else:
        df_old = pd.DataFrame(columns=VWAP_SIGNAL_COLUMNS)

    if not df_old.empty:
        df_old["scan_date"] = df_old["scan_date"].astype(str).str[:10]
        df_old = df_old[df_old["scan_date"] != date]

    df_all = df_new if df_old.empty else pd.concat([df_old, df_new], ignore_index=True)
    if not df_all.empty:
        df_all = df_all.sort_values(["scan_date", "kind", "stock_id", "time"])
    df_all.to_parquet(path, index=False)
    clear_cache()
    return path
