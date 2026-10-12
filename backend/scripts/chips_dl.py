"""Neural-network (MLP) version of the chips model, compared with the GBM on the same split.

Inputs are the feature-table columns + M5 engulfing + chip features; missing values are filled with
the training median and flagged. Trained on rows before --val (2022-07-01 by default), early-stopped
on --val..--split, tested from --split. Several seeds are averaged, since a single net is noisy.

    python -m scripts.chips_dl --table feature_table_2020_2026.parquet --chips chips_daily.parquet
"""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import torch
from torch import nn

from scripts.chips_model import first_per_day, half, load, stat


def prep(df: pd.DataFrame, feats: list[str], fit_rows: pd.Series):
    X = df[feats].astype("float32")
    med = X[fit_rows].median()
    miss = X.isna()
    miss_cols = [c for c in feats if miss.loc[fit_rows, c].mean() > 0.01]
    X = X.fillna(med)
    lo, hi = X[fit_rows].quantile(0.005), X[fit_rows].quantile(0.995)
    X = X.clip(lo, hi, axis=1)
    mu, sd = X[fit_rows].mean(), X[fit_rows].std().replace(0, 1)
    X = (X - mu) / sd
    M = miss[miss_cols].astype("float32").to_numpy()
    return np.hstack([X.to_numpy(np.float32), M])


def train_net(Xtr, ytr, Xva, yva, seed: int, epochs: int = 12) -> nn.Module:
    torch.manual_seed(seed)
    net = nn.Sequential(nn.Linear(Xtr.shape[1], 128), nn.ReLU(), nn.Dropout(0.3),
                        nn.Linear(128, 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, 1))
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-3)
    loss_fn = nn.HuberLoss(delta=2.0)
    Xt, yt = torch.from_numpy(Xtr), torch.from_numpy(ytr)
    Xv = torch.from_numpy(Xva)
    best, best_state, bad = -np.inf, None, 0
    g = torch.Generator().manual_seed(seed)
    for ep in range(epochs):
        net.train()
        perm = torch.randperm(len(Xt), generator=g)
        for i in range(0, len(Xt), 4096):
            idx = perm[i:i + 4096]
            opt.zero_grad()
            loss = loss_fn(net(Xt[idx]).squeeze(1), yt[idx])
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            pv = net(Xv).squeeze(1).numpy()
        # Validation score = mean return of the top 5% of validation rows (what we would trade).
        top = yva[pv >= np.quantile(pv, 0.95)].mean()
        print(f"  seed {seed} epoch {ep + 1}: val top5% {top:+.3f}%", flush=True)
        if top > best:
            best, best_state, bad = top, {k: v.clone() for k, v in net.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= 3:
                break
    net.load_state_dict(best_state)
    net.eval()
    return net


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--val", default="2022-07-01")
    ap.add_argument("--split", default="2023-01-01")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--save-scores", default="")
    args = ap.parse_args()
    torch.set_num_threads(4)

    df, base, eng, chip_cols = load(args.table, args.chips)
    feats = base + eng + chip_cols
    fit = df["date"] < args.val
    X = prep(df, feats, fit)
    tr, va, te = fit.to_numpy(), ((df["date"] >= args.val) & (df["date"] < args.split)).to_numpy(), (df["date"] >= args.split).to_numpy()
    out = df.loc[te, ["date", "stock_id", "minute"]].copy()
    print(f"特徵 {X.shape[1]}（含缺值旗標）；訓練 {tr.sum()}、驗證 {va.sum()}、測試 {te.sum()} 列\n")

    for side, label in (("short", "空"), ("long", "多")):
        y = df[f"{side}_net"].to_numpy(np.float32)
        print(f"## {label}", flush=True)
        p_fit, p_te = np.zeros(tr.sum() + va.sum()), np.zeros(te.sum())
        fitva = tr | va
        for s in range(args.seeds):
            net = train_net(X[tr], y[tr], X[va], y[va], seed=s)
            with torch.no_grad():
                p_fit += net(torch.from_numpy(X[fitva])).squeeze(1).numpy() / args.seeds
                p_te += net(torch.from_numpy(X[te])).squeeze(1).numpy() / args.seeds
        out[f"{side}_nn"] = p_te
        test = df[te]
        print(f"\n| 切點 | 停利3.5/停損3 筆數 | 勝率 | 平均 | 收盤平倉 筆數 | 勝率 | 平均 | 收盤平倉 正的半年 |")
        print("|---|---|---|---|---|---|---|---|")
        for q in (0.05, 0.02):
            thr = np.quantile(p_fit, 1 - q)
            b = first_per_day(test[p_te >= thr])
            hh = b.groupby(half(b["date"]))[f"{side}_hold"].mean()
            print(f"| 前{q * 100:g}% | {stat(b[f'{side}_net'])} | {stat(b[f'{side}_hold'])} | {(hh > 0).sum()}/{len(hh)} |")
        b = first_per_day(test[p_te >= np.quantile(p_fit, 0.98)])
        g = b.groupby(half(b["date"]))[f"{side}_hold"]
        print("\n前 2% 收盤平倉依半年：" + "、".join(f"{h} {v.mean():+.2f}% ({len(v)})" for h, v in g) + "\n", flush=True)

    if args.save_scores:
        out.to_parquet(args.save_scores, index=False)


if __name__ == "__main__":
    main()
