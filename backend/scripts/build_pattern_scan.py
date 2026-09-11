"""Build precomputed D1 pattern-scan parquet shards and optionally upload HF.

Usage:
    python -m scripts.build_pattern_scan --date 2026-09-04
    python -m scripts.build_pattern_scan --init-months 3 --upload

The web/API runtime must read these files instead of running detectors on the
Oracle VM:

    db/pattern_scan/d1/YYYY_MM.parquet
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv

from data.adjustment_query import load_pattern_day
from pattern.offline_store import write_pattern_scan

_ROOT = Path(__file__).parent.parent
_TW = timezone(timedelta(hours=8))
_FLAG_PATHS = [
    _ROOT / "db/adjustment_day_flags/day_flag.parquet",
    _ROOT / "db/d1_flags/day_flag.parquet",
]

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


def _read_trading_dates() -> list[str]:
    for path in _FLAG_PATHS:
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["date"])
        except Exception:
            continue
        dates = sorted({_date_text(value) for value in df["date"].dropna()})
        dates = [
            date
            for date in dates
            if date and pd.Timestamp(date).weekday() < 5
        ]
        if dates:
            return dates
    return []


def _filter_dates(dates: Iterable[str], from_date: str | None, to_date: str | None) -> list[str]:
    filtered = []
    for date in dates:
        if from_date and date < from_date:
            continue
        if to_date and date > to_date:
            continue
        filtered.append(date)
    return filtered


def _target_dates(args: argparse.Namespace) -> list[str]:
    trading_dates = _read_trading_dates()
    if not trading_dates:
        raise RuntimeError("找不到交易日 flag，請先同步 db/adjustment_day_flags 或 db/d1_flags")

    if args.init_months:
        to_bound = args.to_date or _today_tw()
        latest = next((date for date in reversed(trading_dates) if date <= to_bound), None)
        if not latest:
            print(f"{to_bound} 之前沒有可用交易日，略過", flush=True)
            return []
        from_bound = args.from_date
        if not from_bound:
            from_bound = (pd.Timestamp(latest) - pd.DateOffset(months=int(args.init_months))).strftime("%Y-%m-%d")
        return _filter_dates(trading_dates, from_bound, latest)

    if args.from_date or args.to_date:
        from_bound = args.from_date or args.to_date
        to_bound = args.to_date or args.from_date
        return _filter_dates(trading_dates, from_bound, to_bound)

    target = args.date or _today_tw()
    if target not in trading_dates:
        print(f"{target} 不是本機 flag 中的交易日，略過型態掃描", flush=True)
        return []
    return [target]


def _resolve_pattern_types(raw: str) -> list[str]:
    from pattern.pattern_api import DETECTORS

    values = [part.strip() for part in str(raw or "all").split(",") if part.strip()]
    if not values or "all" in values:
        return list(DETECTORS.keys())

    unknown = [value for value in values if value not in DETECTORS]
    if unknown:
        raise RuntimeError(f"未知型態 {unknown}，可用型態：{list(DETECTORS.keys())}")
    return values


def _load_day_groups(
    from_date: str,
    to_date: str,
    stock_ids: set[str],
    limit: int,
) -> dict[str, pd.DataFrame]:
    lookback = (pd.Timestamp(from_date) - pd.tseries.offsets.BDay(max(260, limit * 3))).strftime("%Y-%m-%d")
    df = load_pattern_day(start_date=lookback, end_date=to_date)
    if df.empty:
        return {}
    df["stock_id"] = df["stock_id"].astype(str)
    if stock_ids:
        df = df[df["stock_id"].isin(stock_ids)]
    df = df[df["date"] <= f"{to_date} 23:59:59"]
    return {
        str(stock_id): group.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
        for stock_id, group in df.groupby("stock_id")
    }


def _scan_one_date(
    date: str,
    day_groups: dict[str, pd.DataFrame],
    pattern_types: list[str],
    min_score: float,
    limit: int,
) -> list[dict]:
    from pattern.pattern_api import DETECTORS, STOCK_NAME_MAP, _attach_event_date

    rows: list[dict] = []
    end = pd.Timestamp(f"{date} 23:59:59")
    for stock_id in sorted(day_groups):
        base_df = day_groups[stock_id]
        if base_df.empty:
            continue
        df = base_df[base_df["date"] <= end].tail(limit).reset_index(drop=True)
        if df.empty:
            continue

        stock_name = STOCK_NAME_MAP.get(stock_id, stock_id)
        for pattern_type in pattern_types:
            detector = DETECTORS[pattern_type]
            try:
                detected = detector.detect(df, stock_id=stock_id, timeframe="day")
            except Exception as exc:
                print(f"[WARN] {date} {stock_id} {pattern_type} 偵測失敗：{exc}", flush=True)
                continue
            if not detected or float(detected.score) < min_score:
                continue

            item = detected.to_dict()
            item["scan_date"] = date
            item["stock_name"] = stock_name
            item["name"] = stock_name
            item["pattern_name"] = detector.display_name
            item["in_tick_universe"] = True
            _attach_event_date(item)
            rows.append(item)

    return sorted(
        rows,
        key=lambda row: (
            str(row.get("pattern_type", "")),
            -float(row.get("score") or 0),
            str(row.get("stock_id", "")),
        ),
    )


def build_pattern_scan(
    dates: list[str],
    pattern_types: list[str],
    min_score: float,
    limit: int,
) -> list[Path]:
    if not dates:
        return []

    from pattern.pattern_api import TICK_UNIVERSE_SET

    stock_ids = {str(stock_id) for stock_id in TICK_UNIVERSE_SET}
    if not stock_ids:
        raise RuntimeError("db/tickers/tick_universe.parquet 沒有可用股票，請先同步 tickers")

    day_groups = _load_day_groups(dates[0], dates[-1], stock_ids, limit)
    if not day_groups:
        raise RuntimeError("找不到可掃描的 adjustment_day 日K資料")

    written: list[Path] = []
    for date in dates:
        print(f"掃描 {date} D1 型態（股票 {len(day_groups)} 檔，型態 {len(pattern_types)} 種）...", flush=True)
        rows = _scan_one_date(date, day_groups, pattern_types, min_score, limit)
        path = write_pattern_scan(date, rows)
        written.append(path)
        print(f"完成 {date}：{len(rows)} 筆，寫入 {path.relative_to(_ROOT)}", flush=True)
    return sorted(set(written))


def upload_to_hf(paths: list[Path], repo_id: str | None, token: str | None) -> None:
    if not paths:
        return
    if not repo_id:
        raise RuntimeError("請設定 HF_REPO_ID，或用 --repo-id 指定要上傳的 HF dataset")
    if not token:
        raise RuntimeError("請設定 HF_TOKEN，才能上傳型態掃描結果到 HF")

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    for path in paths:
        path_in_repo = f"db/pattern_scan/d1/{path.name}"
        print(f"上傳 {path.name} -> hf://{repo_id}/{path_in_repo}", flush=True)
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=path_in_repo,
            repo_id=repo_id,
            repo_type="dataset",
            token=token,
            commit_message=f"Update D1 pattern scan {path.stem}",
        )


def sync_to_tidb(paths: list[Path], dates: list[str]) -> None:
    if not paths:
        return
    from data.tidb_offline_store import sync_offline_paths_to_tidb

    summary = sync_offline_paths_to_tidb(paths, dates=dates)
    print(f"TiDB 型態掃描同步完成：{summary or {'pattern_scan': 0}}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline D1 pattern scan results")
    parser.add_argument("--date", default=None, help="掃描單一交易日 YYYY-MM-DD；預設為台北今天")
    parser.add_argument("--from-date", default=None, help="掃描起日 YYYY-MM-DD")
    parser.add_argument("--to-date", default=None, help="掃描迄日 YYYY-MM-DD")
    parser.add_argument("--init-months", type=int, default=0, help="第一次初始化最近 N 個月交易日")
    parser.add_argument("--pattern-types", default="all", help="型態清單，逗號分隔；預設 all")
    parser.add_argument("--min-score", type=float, default=60.0, help="最低型態分數")
    parser.add_argument("--limit", type=int, default=120, help="每支股票 D1 K 線根數")
    parser.add_argument("--upload", action="store_true", help="掃描完成後上傳 monthly parquet shard 到 HF")
    parser.add_argument("--sync-tidb", action="store_true", help="掃描完成後同步到 TiDB 線上查詢表")
    parser.add_argument("--repo-id", default=os.environ.get("HF_REPO_ID"), help="HF dataset repo id")
    parser.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"), help="HF write token")
    args = parser.parse_args()

    if args.init_months < 0:
        raise RuntimeError("--init-months 不可小於 0")
    if args.limit <= 0:
        raise RuntimeError("--limit 必須大於 0")

    dates = _target_dates(args)
    if not dates:
        return
    pattern_types = _resolve_pattern_types(args.pattern_types)
    written = build_pattern_scan(dates, pattern_types, float(args.min_score), int(args.limit))
    if args.upload:
        upload_to_hf(written, args.repo_id, args.hf_token)
    if args.sync_tidb:
        sync_to_tidb(written, dates)


if __name__ == "__main__":
    main()
