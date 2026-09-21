"""Small, file-versioned chart snapshots shared by D1 and M1 requests."""
from collections import OrderedDict
from pathlib import Path
from datetime import datetime, timedelta
import threading

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

_cache = OrderedDict()
_lock = threading.Lock()
_flights = [threading.Lock() for _ in range(16)]
_LIMIT = 64


def file_version(path: Path):
    try:
        stat = path.stat()
        return (str(path), stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return (str(path), None, None)


def cached_frame(key, loader):
    with _flights[hash(key) % len(_flights)]:
        with _lock:
            frame = _cache.get(key)
            if frame is not None:
                _cache.move_to_end(key)
                return frame.copy()
        frame = loader()
        with _lock:
            _cache[key] = frame
            _cache.move_to_end(key)
            while len(_cache) > _LIMIT:
                _cache.popitem(last=False)
        return frame.copy()


def current_session(root: Path, stock_id: str, date: str):
    """Read today's local union; only fall back to HF when no live rows exist.

    Today's prices need no historical adjustment. Keep local batch rows for
    minutes absent in live, with live winning duplicates, as in the slow path.
    """
    live = root / "db/m1_live" / f"{date}.parquet"
    live_version = file_version(live)
    if live_version[1] is None:
        return None
    batch = root / "db/m1" / (date[:7].replace("-", "_") + ".parquet")
    batch_version = file_version(batch)

    def read(path):
        dataset = ds.dataset(str(path), format="parquet")
        start = datetime.fromisoformat(date)
        end = start + timedelta(days=1)
        date_type = dataset.schema.field("date").type
        if pa.types.is_date(date_type):
            start, end = start.date(), end.date()
        elif not pa.types.is_timestamp(date_type):
            start, end = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
        filt = ((ds.field("stock_id") == str(stock_id))
                & (ds.field("date") >= start) & (ds.field("date") < end))
        table = dataset.to_table(filter=filt)
        frame = table.to_pandas()
        frame["date"] = pd.to_datetime(frame["date"], format="mixed")
        return frame[frame["date"].dt.strftime("%Y-%m-%d") == date].reset_index(drop=True)

    # Do not nest flight locks: a batch and session hash can share one stripe.
    batch_frame = None
    if batch_version[1] is not None:
        try:
            batch_frame = cached_frame(("batch", batch_version, stock_id, date), lambda: read(batch))
        except FileNotFoundError:
            return None

    def load_session():
        frame = read(live)
        if frame.empty:
            return frame
        if batch_frame is not None:
            frame = pd.concat([batch_frame, frame], ignore_index=True)
        return frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)

    try:
        frame = cached_frame(("session", live_version, batch_version, stock_id, date), load_session)
    except FileNotFoundError:
        return None  # Collector replaced a file; let the existing fallback retry.
    return None if frame.empty else frame
