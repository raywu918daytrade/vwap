"""Build precomputed intraday VWAP dashboard signal shards and upload to HF.

The Oracle runtime should read these files for historical dates instead of
running whole-market M1 scans on request:

    db/vwap_signals/YYYY_MM.parquet
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

from pattern.vwap_signal_store import write_vwap_signals

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
        dates = [date for date in dates if date and pd.Timestamp(date).weekday() < 5]
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
            print(f"{to_bound} 之前沒有可用交易日，略過盤中訊號產生", flush=True)
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
        print(f"{target} 不是本機 flag 中的交易日，略過盤中訊號產生", flush=True)
        return []
    return [target]


def _build_one_date(date: str, universe: str) -> Path:
    from pattern.vwap_macd_div import divs_map_from_m1 as macd_map_from_m1
    from pattern.vwap_obv_div import divs_map_from_m1 as obv_map_from_m1
    from pattern.vwap_sr_scan import (
        _name_map,
        clear_caches,
        last_chg_map,
        last_m1_bars,
        load_day_m1,
        normalize_universe,
        prev_close_for_date,
        scan_day_events,
        sr_levels_for_date,
    )

    universe = normalize_universe(universe)
    print(f"產生 {date} 盤勢雷達離線訊號（universe={universe}）...", flush=True)
    m1 = load_day_m1(date, universe=universe)
    stocks = set(m1["stock_id"].astype(str)) if m1 is not None and not m1.empty else set()
    levels = sr_levels_for_date(date, stocks) if stocks else {}
    prev_close_map = prev_close_for_date(date)

    vwap, sr = scan_day_events(m1, levels, _name_map(), prev_close_map)
    macd = macd_map_from_m1(m1)
    obv = obv_map_from_m1(m1)
    chg = last_chg_map(date, universe=universe)
    path = write_vwap_signals(
        date,
        vwap=vwap,
        sr=sr,
        macd=macd,
        obv=obv,
        chg=chg,
        sr_levels=levels,
        m1_bars=last_m1_bars(date, universe=universe),
    )
    print(
        f"完成 {date}：VWAP {len(vwap)} / SR {len(sr)} / MACD {len(macd)} / "
        f"OBV {len(obv)} / chg {len(chg)}，寫入 {path.relative_to(_ROOT)}",
        flush=True,
    )
    clear_caches()
    return path


def build_vwap_signals(dates: list[str], universe: str) -> list[Path]:
    written = []
    for date in dates:
        written.append(_build_one_date(date, universe))
    return sorted(set(written))


def upload_to_hf(paths: list[Path], repo_id: str | None, token: str | None) -> None:
    if not paths:
        return
    if not repo_id:
        raise RuntimeError("請設定 HF_REPO_ID，或用 --repo-id 指定要上傳的 HF dataset")
    if not token:
        raise RuntimeError("請設定 HF_TOKEN，才能上傳盤中訊號結果到 HF")

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    for path in paths:
        path_in_repo = f"db/vwap_signals/{path.name}"
        print(f"上傳 {path.name} -> hf://{repo_id}/{path_in_repo}", flush=True)
        api.upload_file(
            path_or_fileobj=str(path),
            path_in_repo=path_in_repo,
            repo_id=repo_id,
            repo_type="dataset",
            token=token,
            commit_message=f"Update VWAP signal bundle {path.stem}",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline VWAP dashboard intraday signals")
    parser.add_argument("--date", default=None, help="產生單一交易日 YYYY-MM-DD；預設為台北今天")
    parser.add_argument("--from-date", default=None, help="產生起日 YYYY-MM-DD")
    parser.add_argument("--to-date", default=None, help="產生迄日 YYYY-MM-DD")
    parser.add_argument("--init-months", type=int, default=0, help="第一次初始化最近 N 個月交易日")
    parser.add_argument("--universe", default="full", choices=["daytrade", "full"], help="預算股票池，預設 full")
    parser.add_argument("--upload", action="store_true", help="產生完成後上傳 monthly parquet shard 到 HF")
    parser.add_argument("--repo-id", default=os.environ.get("HF_REPO_ID"), help="HF dataset repo id")
    parser.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"), help="HF write token")
    args = parser.parse_args()

    if args.init_months < 0:
        raise RuntimeError("--init-months 不可小於 0")

    dates = _target_dates(args)
    if not dates:
        return
    written = build_vwap_signals(dates, args.universe)
    if args.upload:
        upload_to_hf(written, args.repo_id, args.hf_token)


if __name__ == "__main__":
    main()
