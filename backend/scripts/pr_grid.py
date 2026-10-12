"""Original short model (trained on ATR>=5 / PR>=50 before 2023) applied to the wide table with
ATR lowered and the opening-volume PR raised; also how the signals spread over the first minutes.

    python -m scripts.pr_grid --tables 'ft_*.parquet' --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import first_per_day, half, stat
from scripts.wide_study import VOL_COLS, gbm_scores
from scripts.chips_model import NOT_FEATURES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--split", default="2023-01-01")
    args = ap.parse_args()
    df = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    df = df.merge(ch.assign(stock_id=ch["stock_id"].astype(str)), on=["stock_id", "date"], how="left")
    base = [c for c in df.columns if c not in NOT_FEATURES and c not in VOL_COLS and c != "liq_amt20"]
    tr = df[(df["date"] < args.split) & (df["day_atr"] >= 5) & (df["vol5_pr"] >= 50)]
    test = df[df["date"] >= args.split]
    p_tr, p_te = gbm_scores(tr, test, base)
    thr = np.quantile(p_tr, 0.98)
    sig = test[p_te >= thr]
    print(f"最早進場：M5 收盤 {df['time'].min()}（下一根 M1 開盤進場），PR 用 09:00-09:04 五根 M1\n")
    print("## ATR × 開盤量 PR（原模型前 2%，2023-26，收盤平倉，每檔每天第一筆）\n")
    print("| ATR | PR | 成交金額 | 筆數 | 勝率 | 平均 | 正的半年 |")
    print("|---|---|---|---|---|---|---|")
    for atr in (3, 4, 5):
        for pr in (50, 60, 70, 80, 90):
            for liq in (0, 1):
                s = sig[(sig["day_atr"] >= atr) & (sig["vol5_pr"] >= pr) & (sig["liq_amt20"].fillna(0) >= liq)]
                b = first_per_day(s)
                g = b.groupby(half(b["date"]))["short_hold"].mean()
                print(f"| ≥{atr}% | ≥{pr} | {'≥1億' if liq else '不限'} | {stat(b['short_hold'])} | {(g > 0).sum()}/{len(g)} |")
    b = first_per_day(sig[(sig["day_atr"] >= 3) & (sig["vol5_pr"] >= 50)])
    print("\n## 訊號時間分布（ATR≥3 PR≥50，收盤平倉）\n")
    print("| 時段 | 筆數 | 勝率 | 平均 |")
    print("|---|---|---|---|")
    for lo, hi, name in ((0, 10, "09:09"), (10, 20, "09:10-09:19"), (20, 60, "09:20-09:59"), (60, 180, "10:00-11:59"), (180, 999, "12:00 後")):
        g = b[(b["minute"] >= lo) & (b["minute"] < hi)]
        print(f"| {name} | {stat(g['short_hold'])} |")


if __name__ == "__main__":
    main()
