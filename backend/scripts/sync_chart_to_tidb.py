"""Sync adjusted chart candle rows into TiDB.

The runtime reads only one stock/date window from TiDB, so this script keeps
the write side date-scoped too: one trading date is loaded, adjusted, replaced,
and committed at a time.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.dataset as ds
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from data.adjustment_query import _adjust_ohlc, _adjust_volume_only  # noqa: E402
from data.tidb_offline_store import sync_chart_date_to_tidb  # noqa: E402

_ROOT = Path(__file__).parent.parent
_TW = timezone(timedelta(hours=8))
_FLAG_PATHS = [
    _ROOT / "db/adjustment_day_flags/day_flag.parquet",
    _ROOT / "db/d1_flags/day_flag.parquet",
]
_CHART_COLUMNS = ["stock_id", "date", "open", "high", "low", "close", "volume"]

load_dotenv(_ROOT / ".env", override=True)


def _today_tw() -> str:
    return datetime.now(_TW).strftime("%Y-%m-%d")


def _date_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)[:10]


def _month_path(folder: str, date: str) -> Path:
    return _ROOT / "db" / folder / f"{date[:7].replace('-', '_')}.parquet"


def _read_trading_dates() -> list[str]:
    for path in _FLAG_PATHS:
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["date"])
        except Exception:
            continue
        dates = sorted({_date_text(value) for value in df["date"].dropna()})
        dates = [date for date in dates if date and pd.Timestamp(date).weekday() < 5]
        if dates:
            return dates
    return []


def _read_day_dates() -> list[str]:
    """Read the actual dates present in day parquet files.

    The flag file is an incremental-download marker and may contain only a
    subset of the history, so it cannot be used as the source of truth for a
    day-chart backfill.
    """
    paths = sorted((_ROOT / "db/adjustment_day").glob("*.parquet"))
    if not paths:
        return []
    try:
        table = ds.dataset([str(path) for path in paths], format="parquet").to_table(columns=["date"])
    except Exception as exc:
        print(f"[TiDB chart sync] 讀取日 K 日期失敗: {exc}", flush=True)
        return []
    dates = sorted({_date_text(value) for value in table.column("date").to_pylist()})
    return [date for date in dates if date and pd.Timestamp(date).weekday() < 5]


def _filter_dates(values: Iterable[str], from_date: str | None, to_date: str | None) -> list[str]:
    out = []
    for value in values:
        date = _date_text(value)
        if not date:
            continue
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        out.append(date)
    return sorted(set(out))


def _target_dates(args: argparse.Namespace) -> list[str]:
    if args.date:
        return _filter_dates([args.date], args.from_date, args.to_date)

    trading_dates = _read_day_dates() if args.timeframe == "day" else _read_trading_dates()
    if not trading_dates:
        raise RuntimeError("找不到可用交易日，請先同步 chart parquet 與交易日 flag")

    if args.init_months:
        to_bound = args.to_date or _today_tw()
        latest = next((date for date in reversed(trading_dates) if date <= to_bound), None)
        if not latest:
            print(f"{to_bound} 之前沒有可用交易日，略過 chart TiDB 同步", flush=True)
            return []
        from_bound = args.from_date
        if not from_bound:
            from_bound = (pd.Timestamp(latest) - pd.DateOffset(months=int(args.init_months))).strftime("%Y-%m-%d")
        return _filter_dates(trading_dates, from_bound, latest)

    if args.from_date or args.to_date:
        from_bound = args.from_date or args.to_date
        to_bound = args.to_date or args.from_date
        return _filter_dates(trading_dates, from_bound, to_bound)

    return [_today_tw()]


def _read_filtered_parquet(path: Path, filt) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_CHART_COLUMNS)
    try:
        table = ds.dataset(str(path), format="parquet").to_table(
            columns=_CHART_COLUMNS,
            filter=filt,
        )
        if table.num_rows == 0:
            return pd.DataFrame(columns=_CHART_COLUMNS)
        return table.to_pandas()
    except Exception as exc:
        print(f"[TiDB chart sync] pyarrow filter 讀取 {path.name} 失敗，改用 pandas 篩選: {exc}", flush=True)
        df = pd.read_parquet(path, columns=_CHART_COLUMNS)
        return df


def _read_day(date: str) -> tuple[pd.DataFrame, Path]:
    path = _month_path("adjustment_day", date)
    df = _read_filtered_parquet(path, ds.field("date") == date)
    if df.empty:
        return df, path
    df["date"] = pd.to_datetime(df["date"], format="mixed")
    df = df[df["date"].dt.strftime("%Y-%m-%d") == date]
    return _adjust_volume_only(df, None, date, date), path


def _read_m1(date: str) -> tuple[pd.DataFrame, Path]:
    path = _month_path("m1", date)
    start = f"{date} 00:00:00"
    end = f"{date} 23:59:59"
    batch = _read_filtered_parquet(path, (ds.field("date") >= start) & (ds.field("date") <= end))
    live_path = _ROOT / "db" / "m1_live" / f"{date}.parquet"
    live = _read_filtered_parquet(live_path, None) if live_path.exists() else pd.DataFrame(columns=_CHART_COLUMNS)

    frames = []
    for frame in (batch, live):
        if frame.empty:
            continue
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], format="mixed")
        frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=_CHART_COLUMNS), path

    # Match runtime reads: batch first, then live corrections win on duplicate
    # stock/minute rows before the shared adjustment factor is applied.
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["stock_id", "date"], keep="last")
    return _adjust_ohlc(df, date, date), path


def sync_chart_dates(dates: list[str], timeframe: str) -> dict[str, int]:
    summary: dict[str, int] = {}
    include_day = timeframe in {"all", "day"}
    include_m1 = timeframe in {"all", "1m"}
    for date in dates:
        day_df = None
        m1_df = None
        if include_day:
            day_df, day_path = _read_day(date)
            if not day_path.exists():
                print(f"[TiDB chart sync] {date} 缺少 {day_path.relative_to(_ROOT)}，略過 day", flush=True)
                day_df = None
        if include_m1:
            m1_df, m1_path = _read_m1(date)
            if not m1_path.exists():
                print(f"[TiDB chart sync] {date} 缺少 {m1_path.relative_to(_ROOT)}，略過 1m", flush=True)
                m1_df = None
        result = sync_chart_date_to_tidb(
            date,
            day_df=day_df,
            m1_df=m1_df,
            day_source_path=day_path if day_df is not None else None,
            m1_source_path=m1_path if m1_df is not None else None,
        )
        for key, value in result.items():
            summary[key] = summary.get(key, 0) + int(value or 0)
        print(f"TiDB chart 同步 {date}: {result or {'chart_day': 0, 'chart_m1': 0}}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync adjusted day/M1 chart candles into TiDB")
    parser.add_argument("--date", default=None, help="同步單一交易日 YYYY-MM-DD；預設為台北今天")
    parser.add_argument("--from-date", default=None, help="同步起日 YYYY-MM-DD")
    parser.add_argument("--to-date", default=None, help="同步迄日 YYYY-MM-DD")
    parser.add_argument("--init-months", type=int, default=0, help="第一次初始化最近 N 個月交易日")
    parser.add_argument("--timeframe", choices=["all", "day", "1m"], default="all", help="要同步的 chart timeframe")
    args = parser.parse_args()

    if args.init_months < 0:
        raise RuntimeError("--init-months 不可小於 0")

    dates = _target_dates(args)
    if not dates:
        return
    summary = sync_chart_dates(dates, args.timeframe)
    print(f"TiDB chart 同步總結：{summary or {'chart_day': 0, 'chart_m1': 0}}", flush=True)


if __name__ == "__main__":
    main()
