"""Train on Jul-Aug, test on Sep-Oct, over the table from build_feature_table.

Prints (markdown):
1. Baseline: entering at every candidate M5 close.
2. Each feature split into 5 buckets (edges from the training months) or 0/1,
   with train and test win rate / average side by side.
3. A shallow decision tree per side (readable rules), each leaf checked on test.
4. A gradient-boosted model per side: test rows ranked by prediction; the top
   slice, and a tradeable version (first signal per stock-day above the
   training-set threshold), against the baseline.

    python -m scripts.feature_model --table db/feature_table.parquet
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

SPLIT = "2026-09-01"
NOT_FEATURES = {"date", "stock_id", "time", "long_net", "long_exit", "short_net", "short_exit"}


def stat(net: pd.Series) -> str:
    if len(net) == 0:
        return "0 | - | -"
    return f"{len(net)} | {(net > 0).mean() * 100:.1f}% | {net.mean():+.3f}%"


def first_per_day(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["date", "stock_id", "minute"]).groupby(["date", "stock_id"], sort=False).head(1)


def one_at_a_time_net(df: pd.DataFrame, side: str) -> tuple[int, float, int]:
    """One trade per day (the first signal); returns n, sum, max loss streak."""
    df = df.sort_values(["date", "minute"])
    n, total, streak, worst, busy_until = 0, 0.0, 0, 0, ("", -1)
    for r in df.itertuples(index=False):
        if r.date == busy_until[0] and r.minute <= busy_until[1]:
            continue
        net = getattr(r, f"{side}_net")
        n, total = n + 1, total + net
        streak = streak + 1 if net <= 0 else 0
        worst = max(worst, streak)
        busy_until = (r.date, 264)  # unknown exit minute: assume held to the close
    return n, total, worst


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default="db/feature_table.parquet")
    args = parser.parse_args()
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.tree import DecisionTreeRegressor, export_text

    df = pd.read_parquet(args.table)
    feats = [c for c in df.columns if c not in NOT_FEATURES]
    train, test = df[df["date"] < SPLIT], df[df["date"] >= SPLIT]
    print(f"表格 {len(df)} 列；訓練 7-8月 {len(train)} 列 / {train['date'].nunique()} 天；"
          f"測試 9-10月 {len(test)} 列 / {test['date'].nunique()} 天；特徵 {len(feats)} 個\n")

    for side, label in (("short", "空"), ("long", "多")):
        y = f"{side}_net"
        print(f"\n## {label}單\n")
        print("### 基準：每個 5 分鐘點都進場 / 每檔每天第一個點\n")
        print("| | 訓練 筆數 | 勝率 | 平均 | 測試 筆數 | 勝率 | 平均 |")
        print("|---|---|---|---|---|---|---|")
        print(f"| 所有點 | {stat(train[y])} | {stat(test[y])} |")
        print(f"| 每檔每天第一個點 | {stat(first_per_day(train)[y])} | {stat(first_per_day(test)[y])} |")

        print("\n### 單一條件（分 5 組，切點用 7-8 月決定）\n")
        print("| 條件 | 組別 | 訓練 筆數 | 勝率 | 平均 | 測試 筆數 | 勝率 | 平均 | 兩段都 ≥53% |")
        print("|---|---|---|---|---|---|---|---|---|")
        for f in feats:
            col = df[f]
            if col.dropna().nunique() <= 3:
                groups = [(f"= {v:g}", train[f] == v, test[f] == v) for v in sorted(col.dropna().unique())]
            else:
                edges = np.unique(np.nanquantile(train[f], [0, .2, .4, .6, .8, 1]))
                if len(edges) < 3:
                    continue
                edges[0], edges[-1] = -np.inf, np.inf
                groups = []
                for lo, hi in zip(edges[:-1], edges[1:]):
                    name = f"{lo:.2f} ~ {hi:.2f}".replace("-inf", "最低").replace("inf", "最高")
                    groups.append((name, (train[f] >= lo) & (train[f] < hi), (test[f] >= lo) & (test[f] < hi)))
            for name, mtr, mte in groups:
                a, b = train.loc[mtr, y], test.loc[mte, y]
                both = "✓" if len(a) and len(b) and (a > 0).mean() >= .53 and (b > 0).mean() >= .53 else ""
                print(f"| {f} | {name} | {stat(a)} | {stat(b)} | {both} |")

        print("\n### 決策樹（深度 3，每葉至少 2000 列，用 7-8 月訓練）\n")
        tree = DecisionTreeRegressor(max_depth=3, min_samples_leaf=2000, random_state=0)
        X_tr, X_te = train[feats].fillna(-999), test[feats].fillna(-999)
        tree.fit(X_tr, train[y])
        print("```")
        print(export_text(tree, feature_names=feats, decimals=2))
        print("```")
        leaf_tr, leaf_te = tree.apply(X_tr), tree.apply(X_te)
        print("| 葉 | 訓練 筆數 | 勝率 | 平均 | 測試 筆數 | 勝率 | 平均 |")
        print("|---|---|---|---|---|---|---|")
        for leaf in np.unique(leaf_tr):
            print(f"| {leaf} | {stat(train[y][leaf_tr == leaf])} | {stat(test[y][leaf_te == leaf])} |")

        print("\n### 梯度提升模型（7-8 月訓練，9-10 月測試）\n")
        gbm = HistGradientBoostingRegressor(max_depth=4, learning_rate=0.05, max_iter=200,
                                            min_samples_leaf=500, l2_regularization=1.0, random_state=0)
        gbm.fit(train[feats], train[y])
        p_tr, p_te = gbm.predict(train[feats]), gbm.predict(test[feats])
        print("| 預測分數前 | 訓練 筆數 | 勝率 | 平均 | 測試 筆數 | 勝率 | 平均 |")
        print("|---|---|---|---|---|---|---|")
        for q in (0.5, 0.2, 0.1, 0.05, 0.02):
            thr = np.quantile(p_tr, 1 - q)
            print(f"| {q * 100:g}%（訓練切點 {thr:+.3f}） | {stat(train[y][p_tr >= thr])} | {stat(test[y][p_te >= thr])} |")
        print("\n可交易版本：每檔每天只取第一個超過切點的點\n")
        print("| 切點 | 訓練 筆數 | 勝率 | 平均 | 測試 筆數 | 勝率 | 平均 | 測試 每天只做第一筆 筆數 / 合計 / 最多連輸 |")
        print("|---|---|---|---|---|---|---|---|")
        for q in (0.1, 0.05, 0.02):
            thr = np.quantile(p_tr, 1 - q)
            a = first_per_day(train[p_tr >= thr])
            b = first_per_day(test[p_te >= thr])
            n, tot, worst = one_at_a_time_net(b, side)
            print(f"| 前 {q * 100:g}% | {stat(a[y])} | {stat(b[y])} | {n} / {tot:+.1f}% / {worst} |")


if __name__ == "__main__":
    main()
