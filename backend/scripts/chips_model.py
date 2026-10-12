"""Does adding chip features (institutional / margin), M5 engulfing and a liquidity floor help the model?

Same setup as the earlier long-table study: GBM on short_net / long_net (TP3.5 / SL3 label), trained
before --split, tested after; trades = first signal per stock-day above the training-set top-q cutoff;
reported with both exits (TP3.5/SL3 and no-TP / SL3 / hold to close).

    python -m scripts.chips_model --table feature_table_2020_2026.parquet --chips chips_daily.parquet
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

NOT_FEATURES = {"date", "stock_id", "time", "long_net", "long_exit", "short_net", "short_exit", "long_hold", "short_hold"}


def stat(net: pd.Series) -> str:
    if len(net) == 0:
        return "0 | - | -"
    return f"{len(net)} | {(net > 0).mean() * 100:.1f}% | {net.mean():+.3f}%"


def first_per_day(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["date", "stock_id", "minute"]).groupby(["date", "stock_id"], sort=False).head(1)


def half(d: pd.Series) -> pd.Series:
    return d.str[:4] + np.where(d.str[5:7] <= "06", "上", "下")


def add_engulf(df: pd.DataFrame) -> list[str]:
    # Consecutive M5 bars: this bar's open ~= the previous close, so engulfing = opposite colour
    # and a body at least as large as the previous one.
    cur, prev = df["m5_ret"], df["prev_m5_ret"]
    df["m5_bull_engulf"] = ((prev < 0) & (cur > 0) & (cur >= -prev)).astype("int8")
    df["m5_bear_engulf"] = ((prev > 0) & (cur < 0) & (-cur >= prev)).astype("int8")
    df["m5_body_vs_prev"] = (cur.abs() / prev.abs().replace(0, np.nan)).clip(upper=20).astype("float32")
    return ["m5_bull_engulf", "m5_bear_engulf", "m5_body_vs_prev"]


def load(table: str, chips: str) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    df = pd.read_parquet(table)
    df["stock_id"] = df["stock_id"].astype(str)
    base = [c for c in df.columns if c not in NOT_FEATURES]
    eng = add_engulf(df)
    ch = pd.read_parquet(chips)
    ch["stock_id"] = ch["stock_id"].astype(str)
    chip_cols = [c for c in ch.columns if c.startswith("ch_")]
    df = df.merge(ch, on=["stock_id", "date"], how="left")
    return df, base, eng, chip_cols


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, feats: list[str], y: str):
    gbm = HistGradientBoostingRegressor(max_depth=4, learning_rate=0.05, max_iter=200,
                                        min_samples_leaf=500, l2_regularization=1.0, random_state=0)
    gbm.fit(train[feats], train[y])
    return gbm.predict(train[feats]), gbm.predict(test[feats])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--split", default="2023-01-01")
    ap.add_argument("--save-scores", default="")
    args = ap.parse_args()

    df, base, eng, chip_cols = load(args.table, args.chips)
    sd = df.drop_duplicates(["date", "stock_id"])
    print(f"表格 {len(df)} 列，{len(sd)} 個股票日；有法人資料 {sd['ch_f_net1'].notna().mean() * 100:.1f}%，"
          f"有融資資料 {sd['ch_mg_chg1'].notna().mean() * 100:.1f}%\n")
    print("前 20 日平均成交金額（億）分布（股票日）：", sd["liq_amt20"].describe(percentiles=[.1, .25, .5, .75, .9]).round(2).to_dict(), "\n")

    sets = {"A 原特徵": base, "B +吞噬": base + eng, "C +法人融資": base + chip_cols,
            "D 全部": base + eng + chip_cols, "E 全部+成交金額": base + eng + chip_cols + ["liq_amt20", "liq_amt1"]}
    train, test = df[df["date"] < args.split], df[df["date"] >= args.split]
    scores = {}
    for side, label in (("short", "空"), ("long", "多")):
        y, yh = f"{side}_net", f"{side}_hold"
        print(f"\n## {label}：{args.split} 前訓練，之後測試；每檔每天第一筆\n")
        print("| 特徵組 | 切點 | 停利3.5/停損3 筆數 | 勝率 | 平均 | 收盤平倉 筆數 | 勝率 | 平均 | 收盤平倉 正的半年 |")
        print("|---|---|---|---|---|---|---|---|---|")
        for name, feats in sets.items():
            p_tr, p_te = fit_predict(train, test, feats, y)
            scores[(side, name)] = (p_tr, p_te)
            for q in (0.05, 0.02):
                thr = np.quantile(p_tr, 1 - q)
                b = first_per_day(test[p_te >= thr])
                hh = b.groupby(half(b["date"]))[yh].mean()
                print(f"| {name} | 前{q * 100:g}% | {stat(b[y])} | {stat(b[yh])} | {(hh > 0).sum()}/{len(hh)} |")

        # Liquidity floor on the signals of the full model and the original one.
        print(f"\n### {label}：前 2% 訊號依前 20 日平均成交金額分組（收盤平倉）\n")
        print("| 特徵組 | <0.5億 | 0.5-1億 | 1-3億 | 3-10億 | ≥10億 | 只做 ≥1億 |")
        print("|---|---|---|---|---|---|---|")
        for name in ("A 原特徵", "D 全部"):
            p_tr, p_te = scores[(side, name)]
            thr = np.quantile(p_tr, 0.98)
            b = first_per_day(test[p_te >= thr])
            bins = pd.cut(b["liq_amt20"], [-np.inf, 0.5, 1, 3, 10, np.inf], right=False)
            cells = [f"{len(g)} / {(g[yh] > 0).mean() * 100:.0f}% / {g[yh].mean():+.2f}%" if len(g) else "-"
                     for _, g in b.groupby(bins, observed=False)]
            ok = b[b["liq_amt20"] >= 1]
            print(f"| {name} | " + " | ".join(cells) + f" | {len(ok)} / {(ok[yh] > 0).mean() * 100:.0f}% / {ok[yh].mean():+.2f}% |")

        # Half-year detail for the best candidates (hold exit, top 2%).
        print(f"\n### {label}：前 2% 收盤平倉，依半年\n")
        hs = sorted(half(test["date"]).unique())
        print("| 特徵組 | " + " | ".join(hs) + " |")
        print("|---|" + "---|" * len(hs))
        for name in ("A 原特徵", "D 全部", "E 全部+成交金額"):
            p_tr, p_te = scores[(side, name)]
            b = first_per_day(test[p_te >= np.quantile(p_tr, 0.98)])
            g = b.groupby(half(b["date"]))[yh]
            print(f"| {name} | " + " | ".join(f"{g.get_group(h).mean():+.2f}% ({len(g.get_group(h))})" if h in g.groups else "-" for h in hs) + " |")

    # Which chip features matter: single-feature quintiles on the training period vs test, short side.
    print("\n## 單一新特徵分 5 組（空，收盤平倉，所有點；切點用訓練期）\n")
    print("| 特徵 | 組別 | 訓練 筆數 | 勝率 | 平均 | 測試 筆數 | 勝率 | 平均 |")
    print("|---|---|---|---|---|---|---|---|")
    ftr, fte = first_per_day(train), first_per_day(test)
    for f in eng + chip_cols + ["liq_amt20"]:
        if df[f].dropna().nunique() <= 3:
            groups = [(f"= {v:g}", ftr[f] == v, fte[f] == v) for v in sorted(df[f].dropna().unique())]
        else:
            edges = np.unique(np.nanquantile(ftr[f], [0, .2, .4, .6, .8, 1]))
            if len(edges) < 3:
                continue
            edges[0], edges[-1] = -np.inf, np.inf
            groups = [(f"{lo:.3g} ~ {hi:.3g}", (ftr[f] >= lo) & (ftr[f] < hi), (fte[f] >= lo) & (fte[f] < hi))
                      for lo, hi in zip(edges[:-1], edges[1:])]
        for name, a, b in groups:
            print(f"| {f} | {name} | {stat(ftr.loc[a, 'short_hold'])} | {stat(fte.loc[b, 'short_hold'])} |")

    if args.save_scores:
        out = test[["date", "stock_id", "minute"]].copy()
        for (side, name), (_, p) in scores.items():
            out[f"{side}_{name[0]}"] = p
        out.to_parquet(args.save_scores, index=False)


if __name__ == "__main__":
    main()
