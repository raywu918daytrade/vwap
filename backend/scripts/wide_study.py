"""Short side on the wider table (ATR >= 3%, open-5-min volume PR >= 30):

1. Filter grid: ATR 3/4/5% x PR 30/50 (x 20-day turnover >= 1億): baseline and model top 2%.
2. 昨日爆量、今日量縮: previous-day D1 volume spike + today's opening volume smaller than yesterday's.
3. Does adding the volume columns to the model help?

    python -m scripts.wide_study --tables 'ft_*.parquet' --chips chips_daily.parquet
"""

from __future__ import annotations

import argparse
import glob

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from scripts.chips_model import NOT_FEATURES, first_per_day, half, stat

VOL_COLS = ["d1_prev_vol_ratio", "cum_vol_vs_prev_day", "vol5_vs_prev_vol5"]


def gbm_scores(train: pd.DataFrame, test: pd.DataFrame, feats: list[str]):
    m = HistGradientBoostingRegressor(max_depth=4, learning_rate=0.05, max_iter=200,
                                      min_samples_leaf=500, l2_regularization=1.0, random_state=0)
    m.fit(train[feats], train["short_net"])
    return m.predict(train[feats]), m.predict(test[feats])


def halves(b: pd.DataFrame, col: str) -> str:
    g = b.groupby(half(b["date"]))[col].mean()
    return f"{(g > 0).sum()}/{len(g)}"


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
    sd = df.drop_duplicates(["date", "stock_id"])
    print(f"表格 {len(df)} 列，{len(sd)} 個股票日（ATR≥3%、開盤量 PR≥30）\n")

    is_tr = df["date"] < args.split
    train, test = df[is_tr], df[~is_tr]

    # Model trained once on the widest set; each filter only decides which signals get traded.
    p_tr, p_te = gbm_scores(train, test, base)
    test = test.assign(score=p_te)
    # Also: the model trained on the original subset only (ATR>=5, PR>=50), to compare.
    orig_tr = train[(train["day_atr"] >= 5) & (train["vol5_pr"] >= 50)]
    q_tr_o, p_te_o = gbm_scores(orig_tr, test, base)
    test["score_orig"] = p_te_o
    thr_w, thr_o = np.quantile(p_tr, 0.98), np.quantile(q_tr_o, 0.98)

    print("## 1. 門檻比較（空，2023-26 測試，每檔每天第一筆）\n")
    print("基準 = 每檔每天第一個 M5 點都做空；模型 = 分數超過訓練期前 2% 切點的第一筆。出場：收盤平倉（不停利、停損 3%）。\n")
    print("| ATR | 開盤量PR | 成交金額 | 股票日 | 基準 平均 | 寬表模型 筆數 | 勝率 | 平均 | 正的半年 | 原表模型 筆數 | 勝率 | 平均 | 正的半年 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for atr in (3, 4, 5):
        for pr in (30, 50):
            for liq in (0, 1):
                t = test[(test["day_atr"] >= atr) & (test["vol5_pr"] >= pr) & (test["liq_amt20"].fillna(0) >= liq)]
                fb = first_per_day(t)
                a = first_per_day(t[t["score"] >= thr_w])
                o = first_per_day(t[t["score_orig"] >= thr_o])
                print(f"| ≥{atr}% | ≥{pr} | {'≥1億' if liq else '不限'} | {len(fb)} | {fb['short_hold'].mean():+.3f}% | "
                      f"{stat(a['short_hold'])} | {halves(a, 'short_hold')} | {stat(o['short_hold'])} | {halves(o, 'short_hold')} |")

    print("\n## 2. 昨日爆量、今日量縮（空）\n")
    print("條件在 M5 收盤判斷，每檔每天第一次成立就做空（下一根 M1 開盤進場）。訓練期 = 2020-22，測試期 = 2023-26。\n")
    print("爆量 = 昨日 D1 量 / 前 20 日均量；量縮 = 今日開盤 5 分量 / 昨日開盤 5 分量。對照 = 同門檻每檔每天第一個點。\n")
    print("| 門檻 | 爆量 | 量縮 | 再加 | 訓練 筆數 | 勝率 | 平均(收盤平倉) | 測試 筆數 | 勝率 | 平均(收盤平倉) | 測試 停利3.5 平均 | 測試 正的半年 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    extras = {"無": lambda x: pd.Series(True, index=x.index),
              "跌破VWAP": lambda x: x["vs_vwap"] < 0,
              "昨漲>0": lambda x: x["d1_prev_chg"] > 0}
    for atr, pr in ((3, 30), (5, 50)):
        for liq in (0, 1):
            f = df[(df["day_atr"] >= atr) & (df["vol5_pr"] >= pr) & (df["liq_amt20"].fillna(0) >= liq)]
            name = f"ATR≥{atr} PR≥{pr}{' ≥1億' if liq else ''}"
            ctl_tr, ctl_te = first_per_day(f[f["date"] < args.split]), first_per_day(f[f["date"] >= args.split])
            print(f"| {name} | 對照 | - | - | {stat(ctl_tr['short_hold'])} | {stat(ctl_te['short_hold'])} | "
                  f"{ctl_te['short_net'].mean():+.3f}% | {halves(ctl_te, 'short_hold')} |")
            for spike in (2, 3):
                for shrink in (0.7, 0.5):
                    for ename, efn in extras.items():
                        m = (f["d1_prev_vol_ratio"] >= spike) & (f["vol5_vs_prev_vol5"] < shrink) & efn(f)
                        s = f[m]
                        a, b = first_per_day(s[s["date"] < args.split]), first_per_day(s[s["date"] >= args.split])
                        print(f"| {name} | ≥{spike}倍 | <{shrink} | {ename} | {stat(a['short_hold'])} | {stat(b['short_hold'])} | "
                              f"{b['short_net'].mean() if len(b) else float('nan'):+.3f}% | {halves(b, 'short_hold') if len(b) else '-'} |")

    print("\n## 3. 量的欄位加進模型（寬表訓練，前 2%，收盤平倉）\n")
    print("| 特徵 | 門檻 | 筆數 | 勝率 | 平均 | 正的半年 |")
    print("|---|---|---|---|---|---|")
    p_tr2, p_te2 = gbm_scores(train, test, base + VOL_COLS)
    thr2 = np.quantile(p_tr2, 0.98)
    test["score_vol"] = p_te2
    for label, col, thr in (("原特徵", "score", thr_w), ("原特徵+量", "score_vol", thr2)):
        for atr, pr, liq in ((3, 30, 0), (3, 30, 1), (5, 50, 0), (5, 50, 1)):
            t = test[(test["day_atr"] >= atr) & (test["vol5_pr"] >= pr) & (test["liq_amt20"].fillna(0) >= liq)]
            b = first_per_day(t[t[col] >= thr])
            print(f"| {label} | ATR≥{atr} PR≥{pr}{' ≥1億' if liq else ''} | {stat(b['short_hold'])} | {halves(b, 'short_hold')} |")

    print("\n## 半年明細：寬表模型 前 2%，ATR≥3 PR≥30 ≥1億，收盤平倉\n")
    t = test[test["liq_amt20"].fillna(0) >= 1]
    b = first_per_day(t[t["score"] >= thr_w])
    for h, g in b.groupby(half(b["date"])):
        print(f"- {h}：{stat(g['short_hold'])}")


if __name__ == "__main__":
    main()
