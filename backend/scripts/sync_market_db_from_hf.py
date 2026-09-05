"""Synchronize the local market DB from Hugging Face.

`main.live_trader` calls this at startup through
`main.startup_data.sync_local_market_db_from_hf_if_stale()` when the local D1
completion flag is behind the latest expected trading day. It can also be run
manually when local historical data needs to catch up with the dataset pushed by
the GitHub Actions daily job.

By default this mirrors the whole `db/` directory from the HF dataset into the
backend project. Pass `--only` to restrict the pull to selected `db/` children
such as `m1`, `m5_std`, `d1`, `adjustment_day`, `tick`, `tickers`,
`volume_profile`, or `poc_day`.

GitHub Actions intentionally uses `scripts.download_hf_for_gha` instead; that
script pulls a smaller recent subset before running `scripts.update_daily`.

Required environment variables in `backend/.env`:
    HF_REPO_ID : Hugging Face dataset repository id
    HF_TOKEN   : Hugging Face read token, required for private datasets

Examples:
    python -m scripts.sync_market_db_from_hf
    python -m scripts.sync_market_db_from_hf --only m1 m5_std d1 adjustment_day tickers

If HF reports a repeated file-size/consistency mismatch, retry once with xet
disabled:
    HF_HUB_DISABLE_XET=1 python -m scripts.sync_market_db_from_hf
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
from huggingface_hub import snapshot_download

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=True)

HF_REPO_ID = os.environ.get("HF_REPO_ID", "")
HF_TOKEN = os.environ.get("HF_TOKEN") or None

# 本機同步只下載 HF 已產出的 market DB，不重建候選股或分K衍生檔。
# tick_universe.parquet 由 update_daily.py 在 GHA/排程端產出並由
# push_db_to_hf.py 上傳，所以這裡不可排除，否則 daytrade universe 會漂掉。
_IGNORE_PATTERNS = ["*.tmp", "**/*.tmp", "db/m1_live/*"]


def sync_market_db_from_hf(only: list[str] | None = None, repo_id: str | None = None) -> None:
    """Mirror the HF dataset's `db/` files into the local backend project.

    Args:
        only: Optional list of `db/` child folder names to download.
        repo_id: Optional one-off HF dataset repo override. When omitted, the
            value is read from `HF_REPO_ID` in `backend/.env`.
    """
    repo_id = repo_id or HF_REPO_ID
    if not repo_id:
        raise RuntimeError("請在 backend/.env 設定未註解的 HF_REPO_ID，或用 --repo-id 指定要下載的 repo")

    if only:
        allow_patterns = [f"db/{name}/*" for name in only]
        print(f"從 HF Hub（{repo_id}）下載 db/ 的子集：{only} ...")
    else:
        allow_patterns = ["db/*"]
        print(f"從 HF Hub（{repo_id}）下載整個 db/（全量，檔案數多時可能較久，甚至撞到HF rate limit——GHA自動化用的是 scripts/download_hf_for_gha.py，不是這支）...")

    # snapshot_download 本身就有本地快取比對（依檔案 etag/hash），已經下載過
    # 且雲端沒變動的檔案不會重複下載，適合每次都直接呼叫、不用自己維護
    # 「跳過已有月份」這種邏輯。local_dir=_ROOT 讓 repo 裡的 db/... 路徑直接
    # 對應到本機的 db/...（push_db_to_hf.py 用 path_in_repo="db" 上傳，
    # 兩邊路徑結構對稱）。
    snapshot_download(
        repo_id=repo_id,
        repo_type="dataset",
        token=HF_TOKEN,
        allow_patterns=allow_patterns,
        ignore_patterns=_IGNORE_PATTERNS,
        local_dir=str(_ROOT),
    )
    print("完成")


def main(only: list[str] | None = None, repo_id: str | None = None) -> None:
    """CLI-compatible wrapper for manual market DB synchronization."""
    sync_market_db_from_hf(only=only, repo_id=repo_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="+", default=None, help="只下載指定的 db/ 子資料夾，例如 --only m1 d1")
    parser.add_argument("--repo-id", default=None, help="覆蓋 .env 的 HF_REPO_ID，改從指定的其他 HF dataset repo 下載")
    args = parser.parse_args()
    main(only=args.only, repo_id=args.repo_id)
