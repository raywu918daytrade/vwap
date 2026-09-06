"""Synchronize the local market DB from Hugging Face.

`main.live_trader` calls this at startup through
`main.startup_data.sync_local_market_db_from_hf_if_stale()` when the local D1
completion flag is behind the latest expected trading day. It can also be run
manually when local historical data needs to catch up with the externally
maintained HF dataset.

By default this mirrors only the retained market DB folders used by the slim
backend. Long-lived daily datasets are mirrored in full, while large intraday
history folders (`m1`, `m5_std`) are limited to the latest 24 monthly parquet
files. Local live M1 files (`m1_live`) are not downloaded from HF, but are
pruned to the latest 14 trading-date files after scheduled sync checks. Pass
`--only` to override the pull with selected `db/` children such as `m1`,
`m5_std`, `d1`, `adjustment_day`, `pattern_scan`, `vwap_activity`, or
`tickers`.
Runtime logs are kept in their original folders: `logs/` for app/API logs and
`log/` for broker SDK logs. They are pruned after scheduled sync checks and are
not part of HF market DB synchronization.

Required environment variables in `backend/.env`:
    HF_REPO_ID : Hugging Face dataset repository id
    HF_TOKEN   : Hugging Face read token, required for private datasets

Examples:
    python -m scripts.sync_market_db_from_hf
    python -m scripts.sync_market_db_from_hf --only m1 m5_std d1 adjustment_day pattern_scan vwap_activity vwap_signals tickers
    python -m scripts.sync_market_db_from_hf --intraday-months 36
    python -m scripts.sync_market_db_from_hf --m1-live-files 30
    python -m scripts.sync_market_db_from_hf --sdk-log-days 3 --app-log-days 7

If HF reports a repeated file-size/consistency mismatch, retry once with xet
disabled:
    HF_HUB_DISABLE_XET=1 python -m scripts.sync_market_db_from_hf
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from huggingface_hub import snapshot_download

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=True)

HF_REPO_ID = os.environ.get("HF_REPO_ID", "")
HF_TOKEN = os.environ.get("HF_TOKEN") or None
_TW = timezone(timedelta(hours=8))

DEFAULT_MARKET_DB_SYNC_FOLDERS = [
    "m1",
    "m5_std",
    "d1",
    "adjustment_day",
    "tickers",
    "adjustment_factor",
    "tick_adjust_factor",
    "pattern_scan",
    "vwap_activity",
    "vwap_signals",
    "m1_flags",
    "d1_flags",
    "adjustment_day_flags",
]

INTRADAY_RETENTION_FOLDERS = {"m1", "m5_std"}
DEFAULT_INTRADAY_RETENTION_MONTHS = 24
DEFAULT_M1_LIVE_RETENTION_FILES = 14
DEFAULT_SDK_LOG_RETENTION_DAYS = 7
DEFAULT_APP_LOG_RETENTION_DAYS = 14
_MONTH_FILE_RE = re.compile(r"^\d{4}_\d{2}$")
_DATE_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SDK_LOG_FILE_RE = re.compile(r"^(?:client|notify|program)\.log\.(\d{8})$")
_APP_LOG_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")

# 本機同步只下載 HF 已產出的 market DB，不重建候選股或分K衍生檔。
# tick_universe.parquet 由外部資料生產流程產出並同步到 HF，所以這裡不可
# 排除，否則 daytrade universe 會漂掉。
_IGNORE_PATTERNS = ["*.tmp", "**/*.tmp", "db/m1_live/*"]


def _resolve_intraday_months(value: int | None = None) -> int:
    """Return the configured intraday monthly-file retention.

    `0` keeps all remote/local intraday history. Positive values mean "current
    month plus the previous N-1 months", matching the monthly parquet layout.
    """
    if value is None:
        raw = os.environ.get("MARKET_INTRADAY_RETENTION_MONTHS")
        if raw in (None, ""):
            return DEFAULT_INTRADAY_RETENTION_MONTHS
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError("MARKET_INTRADAY_RETENTION_MONTHS 必須是整數") from exc
    if value < 0:
        raise RuntimeError("intraday-months 不可小於 0；0 代表不限制月份")
    return value


def _resolve_m1_live_files(value: int | None = None) -> int:
    """Return how many local live M1 daily parquet files should be retained."""
    if value is None:
        raw = os.environ.get("M1_LIVE_RETENTION_FILES")
        if raw in (None, ""):
            return DEFAULT_M1_LIVE_RETENTION_FILES
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError("M1_LIVE_RETENTION_FILES 必須是整數") from exc
    if value < 0:
        raise RuntimeError("m1-live-files 不可小於 0；0 代表不限制")
    return value


def _resolve_retention_days(
    env_name: str,
    default: int,
    value: int | None = None,
    label: str = "retention-days",
) -> int:
    """Resolve a calendar-day retention value from CLI, env, or default."""
    if value is None:
        raw = os.environ.get(env_name)
        if raw in (None, ""):
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise RuntimeError(f"{env_name} 必須是整數") from exc
    if value < 0:
        raise RuntimeError(f"{label} 不可小於 0；0 代表不限制")
    return value


def _resolve_sdk_log_days(value: int | None = None) -> int:
    """Return calendar days to retain for broker SDK logs in `log/`."""
    return _resolve_retention_days(
        "SDK_LOG_RETENTION_DAYS",
        DEFAULT_SDK_LOG_RETENTION_DAYS,
        value,
        "sdk-log-days",
    )


def _resolve_app_log_days(value: int | None = None) -> int:
    """Return calendar days to retain for app/API JSONL logs in `logs/`."""
    return _resolve_retention_days(
        "APP_LOG_RETENTION_DAYS",
        DEFAULT_APP_LOG_RETENTION_DAYS,
        value,
        "app-log-days",
    )


def _month_stems_to_keep(months: int, now: datetime | None = None) -> list[str]:
    """List monthly parquet stems to keep, oldest first."""
    if months <= 0:
        return []
    now = now or datetime.now()
    current_month_index = now.year * 12 + now.month - 1
    stems = []
    for offset in range(months):
        month_index = current_month_index - offset
        year, month_zero = divmod(month_index, 12)
        stems.append(f"{year}_{month_zero + 1:02d}")
    return sorted(stems)


def _date_stems_to_keep(folder: Path, keep_files: int) -> set[str]:
    """Return the latest date-named parquet stems to keep in a daily folder."""
    if keep_files <= 0 or not folder.exists():
        return set()
    stems = sorted(
        path.stem
        for path in folder.glob("*.parquet")
        if _DATE_FILE_RE.match(path.stem)
    )
    return set(stems[-keep_files:])


def _parse_date_from_file(path: Path, pattern: re.Pattern[str], fmt: str):
    match = pattern.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), fmt).date()
    except ValueError:
        return None


def _prune_dated_files(folder: Path, pattern: re.Pattern[str], fmt: str, retention_days: int) -> int:
    """Delete dated files older than the calendar-day retention window."""
    if retention_days <= 0 or not folder.exists():
        return 0

    cutoff = datetime.now(_TW).date() - timedelta(days=retention_days - 1)
    count = 0
    for path in sorted(folder.iterdir()):
        if not path.is_file():
            continue
        file_date = _parse_date_from_file(path, pattern, fmt)
        if file_date is not None and file_date < cutoff:
            path.unlink()
            count += 1
    return count


def _build_allow_patterns(folders: list[str], intraday_months: int) -> list[str]:
    """Build HF allow_patterns, limiting large intraday folders by month."""
    month_stems = _month_stems_to_keep(intraday_months)
    allow_patterns: list[str] = []
    for name in folders:
        if name in INTRADAY_RETENTION_FOLDERS and intraday_months > 0:
            allow_patterns.extend(f"db/{name}/{stem}.parquet" for stem in month_stems)
        elif name == "pattern_scan":
            allow_patterns.append("db/pattern_scan/**")
        else:
            allow_patterns.append(f"db/{name}/*")
    return allow_patterns


def prune_local_intraday_history(
    folders: list[str] | None = None,
    intraday_months: int | None = None,
) -> dict[str, int]:
    """Delete local monthly parquet files outside the intraday retention window."""
    months = _resolve_intraday_months(intraday_months)
    if months <= 0:
        return {}

    selected = folders or DEFAULT_MARKET_DB_SYNC_FOLDERS
    keep = set(_month_stems_to_keep(months))
    removed: dict[str, int] = {}
    for name in sorted(INTRADAY_RETENTION_FOLDERS.intersection(selected)):
        folder = _ROOT / "db" / name
        count = 0
        if not folder.exists():
            removed[name] = count
            continue
        for path in sorted(folder.glob("*.parquet")):
            if _MONTH_FILE_RE.match(path.stem) and path.stem not in keep:
                path.unlink()
                count += 1
        removed[name] = count
    return removed


def prune_local_m1_live_history(retention_files: int | None = None) -> int:
    """Delete local `db/m1_live` daily parquet files beyond the retention count.

    `m1_live` is runtime data written by the realtime collector and is excluded
    from HF sync. Keeping a short trailing window gives chart fallback for
    recent days when historical M1 is not caught up yet, without growing
    forever on a 24-hour host.
    """
    keep_files = _resolve_m1_live_files(retention_files)
    if keep_files <= 0:
        return 0

    folder = _ROOT / "db/m1_live"
    keep = _date_stems_to_keep(folder, keep_files)
    if not folder.exists():
        return 0

    count = 0
    for path in sorted(folder.glob("*.parquet")):
        if _DATE_FILE_RE.match(path.stem) and path.stem not in keep:
            path.unlink()
            count += 1
    return count


def prune_local_log_history(
    sdk_log_days: int | None = None,
    app_log_days: int | None = None,
) -> dict[str, int]:
    """Prune local runtime logs while keeping them outside `db/`.

    `logs/` is the app/API JSONL log folder read by `/api/logs`. `log/` is used
    by broker SDK/runtime logs. Both are local runtime artifacts and should not
    be uploaded to or downloaded from HF market DB snapshots.
    """
    sdk_days = _resolve_sdk_log_days(sdk_log_days)
    app_days = _resolve_app_log_days(app_log_days)
    return {
        "log": _prune_dated_files(_ROOT / "log", _SDK_LOG_FILE_RE, "%Y%m%d", sdk_days),
        "logs": _prune_dated_files(_ROOT / "logs", _APP_LOG_FILE_RE, "%Y-%m-%d", app_days),
    }


def sync_market_db_from_hf(
    only: list[str] | None = None,
    repo_id: str | None = None,
    intraday_months: int | None = None,
    m1_live_files: int | None = None,
    sdk_log_days: int | None = None,
    app_log_days: int | None = None,
    prune: bool = True,
) -> None:
    """Mirror the HF dataset's `db/` files into the local backend project.

    Args:
        only: Optional list of `db/` child folder names to download. When
            omitted, downloads DEFAULT_MARKET_DB_SYNC_FOLDERS.
        repo_id: Optional one-off HF dataset repo override. When omitted, the
            value is read from `HF_REPO_ID` in `backend/.env`.
        intraday_months: Retention window for large intraday folders. `None`
            uses MARKET_INTRADAY_RETENTION_MONTHS or the default 24 months.
        m1_live_files: Retention count for local `db/m1_live` daily files.
            `None` uses M1_LIVE_RETENTION_FILES or the default 14 files.
        sdk_log_days: Calendar-day retention for broker SDK logs in `log/`.
        app_log_days: Calendar-day retention for app/API logs in `logs/`.
        prune: Whether to delete local old intraday monthly parquet files after
            the HF sync, and prune `m1_live` and logs. Defaults to True so
            long-running hosts stay slim.
    """
    repo_id = repo_id or HF_REPO_ID
    if not repo_id:
        raise RuntimeError("請在 backend/.env 設定未註解的 HF_REPO_ID，或用 --repo-id 指定要下載的 repo")

    folders = only or DEFAULT_MARKET_DB_SYNC_FOLDERS
    months = _resolve_intraday_months(intraday_months)
    allow_patterns = _build_allow_patterns(folders, months)
    label = "指定子集" if only else "預設保留子集"
    print(f"從 HF Hub（{repo_id}）下載 db/ 的{label}：{folders} ...")
    limited = sorted(INTRADAY_RETENTION_FOLDERS.intersection(folders))
    if months > 0 and limited:
        month_stems = _month_stems_to_keep(months)
        print(
            f"其中 {limited} 只同步最近 {months} 個月份：{month_stems[0]} ~ {month_stems[-1]}",
            flush=True,
        )

    # snapshot_download 本身就有本地快取比對（依檔案 etag/hash），已經下載過
    # 且雲端沒變動的檔案不會重複下載，適合每次都直接呼叫、不用自己維護
    # 「跳過已有月份」這種邏輯。local_dir=_ROOT 讓 repo 裡的 db/... 路徑直接
    # 對應到本機的 db/...；HF dataset 也以 db/... 作為根路徑，兩邊結構對稱。
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        token=HF_TOKEN,
        allow_patterns=allow_patterns,
        ignore_patterns=_IGNORE_PATTERNS,
        local_dir=str(_ROOT),
    )
    if prune:
        removed = prune_local_intraday_history(folders=list(folders), intraday_months=months)
        for name, count in removed.items():
            if count:
                print(f"已刪除 db/{name}/ 超過保留期限的月檔 {count} 個", flush=True)
        m1_live_removed = prune_local_m1_live_history(m1_live_files)
        if m1_live_removed:
            keep_files = _resolve_m1_live_files(m1_live_files)
            print(f"已刪除 db/m1_live/ 超過最近 {keep_files} 個交易檔的舊檔 {m1_live_removed} 個", flush=True)
        log_removed = prune_local_log_history(sdk_log_days=sdk_log_days, app_log_days=app_log_days)
        if log_removed.get("log"):
            days = _resolve_sdk_log_days(sdk_log_days)
            print(f"已刪除 log/ 超過最近 {days} 個日曆天的舊日誌 {log_removed['log']} 個", flush=True)
        if log_removed.get("logs"):
            days = _resolve_app_log_days(app_log_days)
            print(f"已刪除 logs/ 超過最近 {days} 個日曆天的舊日誌 {log_removed['logs']} 個", flush=True)
    print("完成")


def main(
    only: list[str] | None = None,
    repo_id: str | None = None,
    intraday_months: int | None = None,
    m1_live_files: int | None = None,
    sdk_log_days: int | None = None,
    app_log_days: int | None = None,
    prune: bool = True,
) -> None:
    """CLI-compatible wrapper for manual market DB synchronization."""
    sync_market_db_from_hf(
        only=only,
        repo_id=repo_id,
        intraday_months=intraday_months,
        m1_live_files=m1_live_files,
        sdk_log_days=sdk_log_days,
        app_log_days=app_log_days,
        prune=prune,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", default=None, help="只下載指定的 db/ 子資料夾，例如 --only m1 d1")
    parser.add_argument("--repo-id", default=None, help="覆蓋 .env 的 HF_REPO_ID，改從指定的其他 HF dataset repo 下載")
    parser.add_argument("--intraday-months", type=int, default=None, help="m1/m5_std 只下載並保留最近 N 個月；0 表示不限制")
    parser.add_argument("--m1-live-files", type=int, default=None, help="m1_live 只保留最近 N 個交易日檔；0 表示不限制")
    parser.add_argument("--sdk-log-days", type=int, default=None, help="log/ 只保留最近 N 個日曆天；0 表示不限制")
    parser.add_argument("--app-log-days", type=int, default=None, help="logs/ 只保留最近 N 個日曆天；0 表示不限制")
    parser.add_argument("--no-prune", action="store_true", help="下載後不刪除本機超過期限的 m1/m5_std 月檔，也不清 m1_live/logs")
    args = parser.parse_args()
    main(
        only=args.only,
        repo_id=args.repo_id,
        intraday_months=args.intraday_months,
        m1_live_files=args.m1_live_files,
        sdk_log_days=args.sdk_log_days,
        app_log_days=args.app_log_days,
        prune=not args.no_prune,
    )
