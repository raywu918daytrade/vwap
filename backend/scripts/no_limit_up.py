"""One-at-a-time short model without signals that could get stuck at limit-up.

Taiwan limit-up is +10% from the previous close. A short entered at +x% has its 3% stop at
(1+x)*1.03 - 1; once that is past +10% (x >= ~6.8%) the stop can never trigger and a locked
limit-up may leave the short uncovered. Variants exclude such entries and stocks that already
touched ~limit-up today; model either reused (trained on all rows) or retrained without them.

    python -m scripts.no_limit_up --tables 'ft_*.parquet'
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import NOT_FEATURES, half
from scripts.one_at_a_time import doubling, flat_dd, streaks
from scripts.wide_study import VOL_COLS, gbm_scores


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--split", default="2023-01-01")
    args = ap.parse_args()
    df = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.tables))], ignore_index=True)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50)].copy()
    df["day_high_chg"] = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    base = [c for c in df.columns if c not in NOT_FEATURES and c not in VOL_COLS and c != "day_high_chg"]
    near = df["chg_from_prev_close"] >= 6.8
    touched = df["day_high_chg"] >= 9.5
    is_tr = df["date"] < args.split
    n_days = df.loc[~is_tr, "date"].nunique()

    variants = {
        "原本（不排除）": (pd.Series(False, index=df.index), False),
        "排除 漲≥6.8%": (near, False),
        "排除 漲≥6.8% 或 今日碰過漲停": (near | touched, False),
        "排除 漲≥6.8% 或 今日碰過漲停（重新訓練）": (near | touched, True),
    }
    for cap in (5, 3, 0):
        ex = (df["chg_from_prev_close"] > cap) | touched
        variants[f"只空 漲≤{cap}%（未碰漲停）"] = (ex, False)
        variants[f"只空 漲≤{cap}%（未碰漲停，重新訓練）"] = (ex, True)
    full_tr, full_te = gbm_scores(df[is_tr], df[~is_tr], base)
    print("# 排除可能卡漲停的訊號（做空，ATR≥5 PR≥50，前 2%，一次一筆，收盤平倉、停損 3%，2023-26）\n")
    print("| 版本 | 交易天數 | 勝率 | 平均 | 合計 | 最大回落 | 最多連輸 | 正的半年 | 每半年合計 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, (excl, retrain) in variants.items():
        keep = ~excl
        if retrain:
            p_tr, p_te = gbm_scores(df[is_tr & keep], df[~is_tr], base)
        else:
            p_tr, p_te = full_tr[keep[is_tr].to_numpy()], full_te
        thr = np.quantile(p_tr, 0.98)
        te = df[~is_tr].assign(score=p_te)
        s = te[(te["score"] >= thr) & keep[~is_tr]]
        s = s.sort_values(["date", "minute", "score"], ascending=[True, True, False]).groupby("date").head(1)
        r = s["short_hold"].to_numpy(float)
        _, bl, _ = streaks([bool(x) for x in r > 0])
        hh = s.groupby(half(s["date"]))["short_hold"].sum()
        print(f"| {name} | {len(s)}/{n_days} | {(r > 0).mean() * 100:.1f}% | {r.mean():+.3f}% | {r.sum():+.1f}% | "
              f"{flat_dd(r):.1f}% | {bl} | {(hh > 0).sum()}/{len(hh)} | " + "、".join(f"{h} {v:+.0f}%" for h, v in hh.items()) + " |")
        if retrain:
            cols = ["minute", "chg_from_prev_close", "vs_vwap", "d1_prev_chg", "d1_vs_ma20", "vol5_pr", "m5_ret"]
            print(f"\n  （{name} 選到的訊號中位數：" + "、".join(f"{c} {s[c].median():.2f}" for c in cols) + "）\n")


if __name__ == "__main__":
    main()
