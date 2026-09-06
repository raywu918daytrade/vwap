"""VWAP 兩欄前端過濾用的每股活動度：日ATR%、開盤 5 分幅%、09:05 量 PR。

三個數都是相對量，高低價股同一套門檻。日 ATR = 過去 14 個交易日 TR 平均
（算到昨天）/ 今天開盤。
量 PR = 今日 09:00–09:05 volume vs 自己過去 20 個交易日同一段。
"""

from __future__ import annotations

import threading
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

_ROOT = Path(__file__).resolve().parent.parent
_TW = timezone(timedelta(hours=8))
_ENTRY = dtime(9, 5)
_LOOKBACK = 20
_MIN_HIST = 10
_ATR_N = 14

_cache: dict[tuple[str, str], dict[str, dict]] = {}
_lock = threading.Lock()


def _is_weekend(date_str: str) -> bool:
    try:
        return pd.Timestamp(date_str).weekday() >= 5
    except Exception:
        return False


def clear_cache() -> None:
    """Clear cached activity metrics after HF-synced parquet files change."""
    with _lock:
        _cache.clear()


def _round_or_none(x, n: int):
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), n)


def _day_atr14(day: pd.DataFrame) -> pd.DataFrame:
    """在日K上算 _atr14（含當列那天的 TR）；相對今開還要再 shift 到 D。"""
    day = day.sort_values(["stock_id", "date"]).copy()
    prev = day.groupby("stock_id", sort=False)["close"].shift(1)
    tr = np.maximum(
        np.maximum((day["high"] - day["low"]).abs(), (day["high"] - prev).abs()),
        (day["low"] - prev).abs(),
    )
    day["_atr14"] = tr.groupby(day["stock_id"]).transform(
        lambda s: s.rolling(_ATR_N, min_periods=_ATR_N).mean()
    )
    return day


def _stock_ids_for_universe(universe: str | None) -> tuple[str, set[str]]:
    from pattern.vwap_sr_scan import normalize_universe, stock_ids_for_universe

    normalized = normalize_universe(universe)
    return normalized, stock_ids_for_universe(normalized)


def _filter_stock_ids(df: pd.DataFrame, stock_ids: set[str]) -> pd.DataFrame:
    if not stock_ids or df is None or df.empty:
        return df
    return df[df["stock_id"].astype(str).isin(stock_ids)].reset_index(drop=True)


def _open5_from_m1(date_str: str, stock_ids: set[str]) -> pd.DataFrame:
    """盤中 m5_std 還沒今天 09:05 時，用 m1_live 09:01–09:05 合成一根。"""
    from data.query import load_m1_live

    m1 = load_m1_live(date_str)
    empty = pd.DataFrame(columns=["stock_id", "open", "high", "low", "volume"])
    if m1 is None or m1.empty:
        return empty
    m1 = m1.copy()
    m1["stock_id"] = m1["stock_id"].astype(str)
    m1 = _filter_stock_ids(m1, stock_ids)
    if m1.empty:
        return empty
    m1["date"] = pd.to_datetime(m1["date"], format="mixed")
    t = m1["date"].dt.time
    m1 = m1[(t >= dtime(9, 1)) & (t <= _ENTRY)]
    if m1.empty:
        return empty
    g = m1.sort_values("date").groupby("stock_id", sort=False)
    out = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "volume": g["volume"].sum(),
        }
    ).reset_index()
    out["stock_id"] = out["stock_id"].astype(str)
    return out


def _load_m5_905(date_str: str, hist_start: str) -> pd.DataFrame:
    """Read only each trading day's 09:05 M5 bar for activity PR.

    `load_m5_std(start_date=...)` materializes every 5-minute bar in the
    lookback window. On a 1GB Oracle VM that is the difference between a small
    lookup and a request that can starve the API worker.
    """
    columns = ["stock_id", "date", "open", "high", "low", "volume"]
    empty = pd.DataFrame(columns=[*columns, "day"])
    try:
        from data import raw_query
        from data.query import _adjust_ohlc

        paths = raw_query._dataset_paths(_ROOT / "db/m5_std", hist_start, date_str)
        if not paths:
            return empty
        timestamps = [
            pd.Timestamp(day).replace(hour=9, minute=5).to_pydatetime()
            for day in pd.date_range(hist_start, date_str, freq="D")
        ]
        table = ds.dataset(paths, format="parquet").to_table(
            filter=ds.field("date").isin(timestamps),
            columns=columns,
        )
        if table.num_rows == 0:
            return empty
        m5 = table.to_pandas()
        m5 = _adjust_ohlc(m5, hist_start)
    except Exception:
        from data.query import load_m5_std

        m5 = load_m5_std(start_date=hist_start)

    if m5 is None or m5.empty:
        return empty
    m5 = m5.copy()
    m5["stock_id"] = m5["stock_id"].astype(str)
    m5["date"] = pd.to_datetime(m5["date"], format="mixed")
    end_excl = pd.Timestamp(date_str) + pd.Timedelta(days=1)
    m5 = m5[
        (m5["date"] >= pd.Timestamp(hist_start))
        & (m5["date"] < end_excl)
        & (m5["date"].dt.hour == 9)
        & (m5["date"].dt.minute == 5)
    ]
    if m5.empty:
        return empty
    m5["day"] = m5["date"].dt.strftime("%Y-%m-%d")
    return m5.drop_duplicates(["stock_id", "day"], keep="last").reset_index(drop=True)


def _vol5_pr_map(
    m5_905: pd.DataFrame, date_str: str, extra_today: pd.DataFrame | None
) -> dict[str, float]:
    hist = m5_905[["stock_id", "day", "volume"]].copy() if not m5_905.empty else pd.DataFrame(
        columns=["stock_id", "day", "volume"]
    )
    if extra_today is not None and not extra_today.empty:
        extra = extra_today[["stock_id", "volume"]].copy()
        extra["day"] = date_str
        extra["stock_id"] = extra["stock_id"].astype(str)
        have = set()
        if not hist.empty:
            have = set(hist.loc[hist["day"] == date_str, "stock_id"].astype(str))
        extra = extra[~extra["stock_id"].isin(have)]
        hist = pd.concat([hist, extra[["stock_id", "day", "volume"]]], ignore_index=True)
    out: dict[str, float] = {}
    if hist.empty:
        return out
    hist["stock_id"] = hist["stock_id"].astype(str)
    for sid, g in hist.groupby("stock_id", sort=False):
        g = g.sort_values("day")
        today = g.loc[g["day"] == date_str, "volume"]
        if today.empty:
            continue
        past = g.loc[g["day"] < date_str, "volume"].tail(_LOOKBACK).to_numpy(dtype=float)
        cur = float(today.iloc[-1])
        if len(past) < _MIN_HIST or not np.isfinite(cur):
            continue
        out[str(sid)] = float(np.mean(past < cur))
    return out


def _as_open_map(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float)
    d = df.drop_duplicates("stock_id").copy()
    d["stock_id"] = d["stock_id"].astype(str)
    return d.set_index("stock_id")["open"].astype(float)


def compute_activity_metrics(date_str: str, universe: str = "full") -> dict[str, dict]:
    """Compute activity metrics from historical inputs for offline jobs."""
    from data.adjustment_query import load_pattern_day

    universe, stock_ids = _stock_ids_for_universe(universe)
    del universe
    hist_start = (pd.Timestamp(date_str) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    end_excl = pd.Timestamp(date_str) + pd.Timedelta(days=1)

    atr_asof = pd.Series(dtype=float)
    open_d = pd.Series(dtype=float)
    day = load_pattern_day(start_date=hist_start, end_date=date_str)
    if not day.empty:
        day = day.copy()
        day["stock_id"] = day["stock_id"].astype(str)
        day = _filter_stock_ids(day, stock_ids)
        day["date"] = pd.to_datetime(day["date"], format="mixed")
        day["day"] = day["date"].dt.strftime("%Y-%m-%d")
        day = day[(day["date"] < end_excl) & (day["open"] > 0)]
        if not day.empty:
            day = _day_atr14(day)
            before = day[day["day"] < date_str]
            if not before.empty:
                atr_asof = before.sort_values("date").groupby("stock_id")["_atr14"].last()
                atr_asof.index = atr_asof.index.astype(str)
            today_day = day[day["day"] == date_str]
            if not today_day.empty:
                open_d = _as_open_map(today_day)

    m5_905 = _filter_stock_ids(_load_m5_905(date_str, hist_start), stock_ids)

    extra = _open5_from_m1(date_str, stock_ids)
    today_m5 = m5_905[m5_905["day"] == date_str] if not m5_905.empty else m5_905
    if today_m5 is not None and not today_m5.empty:
        today_bar = today_m5[["stock_id", "open", "high", "low", "volume"]].copy()
        extra_for_pr = None
        if extra is not None and not extra.empty:
            have = set(today_bar["stock_id"].astype(str))
            add = extra[~extra["stock_id"].astype(str).isin(have)]
            if not add.empty:
                today_bar = pd.concat(
                    [today_bar, add[["stock_id", "open", "high", "low", "volume"]]],
                    ignore_index=True,
                )
                extra_for_pr = add
    else:
        today_bar = extra
        extra_for_pr = extra

    m5_open = _as_open_map(today_bar)
    if open_d.empty:
        open_d = m5_open
    else:
        fill = m5_open.loc[~m5_open.index.isin(open_d.index)]
        if not fill.empty:
            open_d = pd.concat([open_d, fill])

    open5: dict[str, float] = {}
    if today_bar is not None and not today_bar.empty:
        tb = today_bar.copy()
        tb["stock_id"] = tb["stock_id"].astype(str)
        rng = (tb["high"].astype(float) - tb["low"].astype(float)) / tb["open"].replace(0, np.nan)
        for sid, val in zip(tb["stock_id"], rng):
            if np.isfinite(val):
                open5[str(sid)] = float(val)

    vol_pr = _vol5_pr_map(m5_905, date_str, extra_for_pr)

    day_atr: dict[str, float] = {}
    if not atr_asof.empty and not open_d.empty:
        a, o = atr_asof.align(open_d, join="inner")
        pct = a / o.replace(0, np.nan)
        for sid, val in pct.items():
            if np.isfinite(val):
                day_atr[str(sid)] = float(val)

    out: dict[str, dict] = {}
    for sid in set(day_atr) | set(open5) | set(vol_pr):
        out[sid] = {
            "day_atr": _round_or_none(day_atr.get(sid), 5),
            "open5_rng": _round_or_none(open5.get(sid), 5),
            "vol5_pr": _round_or_none(vol_pr.get(sid), 4),
        }
    return out


def metrics_for_date(date_str: str, universe: str = "daytrade") -> dict[str, dict]:
    universe, stock_ids = _stock_ids_for_universe(universe)
    key = (date_str, universe)
    today = datetime.now(_TW).strftime("%Y-%m-%d")
    with _lock:
        if date_str != today and key in _cache:
            return _cache[key]

    from pattern.activity_store import has_activity_store, read_vwap_activity

    offline = read_vwap_activity(date_str, stock_ids=stock_ids)
    if offline is not None:
        if offline or date_str != today or _is_weekend(date_str):
            if date_str != today:
                with _lock:
                    _cache[key] = offline
            return offline
    if date_str != today and has_activity_store():
        with _lock:
            _cache[key] = {}
        return {}
    if date_str == today and _is_weekend(date_str):
        return {}

    result = compute_activity_metrics(date_str, universe)
    if date_str != today:
        with _lock:
            _cache[key] = result
    return result
