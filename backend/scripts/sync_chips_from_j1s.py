"""
把法人（db/ib）、融資券（db/margin）從 raywu918python/j1s-data 複製到本專案的
HF dataset（預設 raywu918daytrade/daytrade）。

這兩份資料由 j1stools_source 的 update_ib.yml / update_margin.yml 每天更新到
j1s-data；daytrade 這邊原本只在 2026-08-16 手動複製過一次，之後沒有任何排程
同步，所以一直停在舊資料。這支腳本補上每日同步。

用法：
    python -m scripts.sync_chips_from_j1s            # 只同步最近 2 個月
    python -m scripts.sync_chips_from_j1s --months 0 # 全部重新同步
"""

import argparse
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

from huggingface_hub import HfApi, hf_hub_download

SRC_REPO = "raywu918python/j1s-data"
DST_REPO = os.environ.get("HF_REPO_ID") or "raywu918daytrade/daytrade"
FOLDERS = ["db/ib", "db/margin"]
_MONTH_FILE = re.compile(r"/(\d{4})_(\d{1,2})\.parquet$")


def _recent_months(n: int) -> set[tuple[int, int]]:
    now = datetime.now(timezone(timedelta(hours=8)))
    y, m = now.year, now.month
    out = set()
    for _ in range(n):
        out.add((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def main(months: int):
    token = os.environ["HF_TOKEN"]
    api = HfApi(token=token)
    src_files = api.list_repo_files(SRC_REPO, repo_type="dataset")
    keep = _recent_months(months) if months > 0 else None

    picked = []
    for path in src_files:
        if not any(path.startswith(f + "/") for f in FOLDERS):
            continue
        match = _MONTH_FILE.search(path)
        if not match:
            continue
        if keep is not None and (int(match.group(1)), int(match.group(2))) not in keep:
            continue
        picked.append(path)
    if not picked:
        raise RuntimeError(f"{SRC_REPO} 找不到要同步的檔案")

    with tempfile.TemporaryDirectory() as tmp:
        for path in picked:
            hf_hub_download(SRC_REPO, path, repo_type="dataset", local_dir=tmp, token=token)
        print(f"下載 {len(picked)} 個檔案，上傳到 {DST_REPO}")
        api.upload_folder(
            repo_id=DST_REPO,
            repo_type="dataset",
            folder_path=tmp,
            allow_patterns=[f"{f}/*.parquet" for f in FOLDERS],
            commit_message=f"Sync db/ib, db/margin from {SRC_REPO}",
        )
    for path in sorted(picked):
        print("  ", path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=2, help="同步最近幾個月；0 = 全部")
    main(parser.parse_args().months)
