"""Ray 2026-10-18: DL model on the M-top -> MACD-bounce short entries.
Features he asked for: M top / neckline position vs today's open, signal time, day change at the signal,
opening gap, the shape of the current red MACD block (the bounce) and of the two red blocks before it
(the ones at the M top), plus the M geometry. Train < --split (default 2024-01-01), test from --split.
Small sample (~2k entries), so: MLP averaged over seeds, LightGBM and logistic regression as baselines.

    python -m scripts.mtop_dl --entries mtop_bdiv_all_7y.parquet --dir .../mtop/wide --years 2020-2026
"""

import argparse

import numpy as np
import pandas as pd
import torch
from torch import nn

from scripts.chips_model import half
from scripts.macd_div_study import macd_hist
from scripts.mtop_bounce_div import blocks, load_raw
from scripts.mtop_shape import shape_metrics


def block_feats(hist, b, upto, px, pre):
    """Shape of one histogram block (start, end, peak) as seen up to bar `upto`: peak, length, area, bars to peak (in % of price)."""
    s, e = b[0], min(b[1], upto)
    seg = hist[s:e + 1] / px * 100
    return {f"{pre}_peak": seg.max(), f"{pre}_len": e - s + 1, f"{pre}_area": seg.sum(), f"{pre}_to_peak": int(np.argmax(seg)),
            f"{pre}_last": seg[-1]}


def build(ent, raw, paths):
    ent = ent[ent["rr1_m"].notna()].copy()
    ent["stock_id"] = ent["stock_id"].astype(str)
    cols = ["date", "stock_id", "l0_time", "p1_time", "tr_time", "p2_time", "d_time", "d_price", "time", "p1_hi", "p2_hi", "diff_pct", "rise_pct"]
    have = [c for c in cols if c not in ent.columns or c in ("date", "stock_id")]
    ent = ent.merge(raw[have].rename(columns={"time": "brk_time2"}), on=["date", "stock_id"], how="left")
    g = paths.groupby(["date", "stock_id"])
    rows = []
    for _, r in ent.iterrows():
        b = g.get_group((r["date"], r["stock_id"])).sort_values("time").reset_index(drop=True)
        times = b["time"].tolist()
        o, h, l, c = (b[k].to_numpy(float) for k in "ohlc")
        hist = macd_hist(c)
        u = times.index(r["time"])                      # signal bar (entry = next open)
        t = times.index(r["brk_time2"] if "brk_time2" in r else r["brk_time"])
        p1, p2, tr = times.index(r["p1_time"]), times.index(r["p2_time"]), times.index(r["tr_time"])
        day_open, pc, px = o[0], float(r["prev_close"]), c[u]
        f = {"date": r["date"], "stock_id": r["stock_id"], "rr1_m": r["rr1_m"], "hold_m": r["hold_m"], "tp35_m": r["tp35_m"],
             "peak_ratio": r.get("peak_ratio", np.nan), "day_atr": r["day_atr"], "vol5_pr": r["vol5_pr"],
             # positions vs open / prev close
             "mhi_vs_open": (float(r["m_hi"]) / day_open - 1) * 100, "neck_vs_open": (float(r["neck"]) / day_open - 1) * 100,
             "px_vs_open": (px / day_open - 1) * 100, "open_gap": (day_open / pc - 1) * 100, "px_vs_pc": (px / pc - 1) * 100,
             "hi_sofar_pc": (h[:u + 1].max() / pc - 1) * 100, "lo_sofar_pc": (l[:u + 1].min() / pc - 1) * 100,
             "px_vs_vwap": (px / float(b["vwap"].iloc[u]) - 1) * 100,
             # time
             "t_min": int(times[u][:2]) * 60 + int(times[u][3:]) - 540, "bars_since_brk": u - t, "bars_since_p2": u - p2,
             # M geometry
             "depth_pct": r["depth_pct"], "top_diff_pct": r["diff_pct"], "rise_pct": r["rise_pct"], "m_width": p2 - times.index(r["l0_time"]),
             "bounce_hi_vs_neck": (h[t + 1:u + 1].max() / float(r["neck"]) - 1) * 100 if u > t else 0.0,
             "sl_m": r["sl_m"]}
        f.update(shape_metrics(b, r) or {})
        # MACD blocks: current (bounce) + the two red blocks before it
        bl = blocks(hist)
        cur = next((x for x in bl if x[0] <= u <= x[1]), None)
        if cur is None:
            continue
        prev = [x for x in bl if x[1] < cur[0]][-2:]
        f.update(block_feats(hist, cur, u, px, "cur"))
        for k, x in enumerate(reversed(prev), 1):
            f.update(block_feats(hist, x, x[1], px, f"prev{k}"))
        if len(prev) >= 1:
            f["cur_vs_prev1"] = f["cur_peak"] / f["prev1_peak"] if f["prev1_peak"] > 0 else np.nan
        if len(prev) == 2:
            f["prev1_vs_prev2"] = f["prev1_peak"] / f["prev2_peak"] if f["prev2_peak"] > 0 else np.nan
        # the green block between prev1 and cur (the drop that broke the neckline)
        gseg = hist[prev[-1][1] + 1:cur[0]] / px * 100 if prev else np.array([])
        f["green_min"], f["green_len"], f["green_area"] = (gseg.min(), len(gseg), gseg.sum()) if len(gseg) else (np.nan, 0, np.nan)
        rows.append(f)
    return pd.DataFrame(rows)


def prep(df, feats, fit):
    X = df[feats].astype("float32")
    med = X[fit].median()
    miss = X.isna()
    X = X.fillna(med)
    lo, hi = X[fit].quantile(0.005), X[fit].quantile(0.995)
    X = X.clip(lo, hi, axis=1)
    mu, sd = X[fit].mean(), X[fit].std().replace(0, 1)
    X = (X - mu) / sd
    mc = [c for c in feats if miss.loc[fit, c].mean() > 0.01]
    return np.hstack([X.to_numpy(np.float32), miss[mc].astype("float32").to_numpy()])


def train_net(Xtr, ytr, seed, epochs=200):
    torch.manual_seed(seed)
    net = nn.Sequential(nn.Linear(Xtr.shape[1], 64), nn.ReLU(), nn.Dropout(0.3), nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.3), nn.Linear(32, 1))
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=1e-2)
    Xt, yt = torch.from_numpy(Xtr), torch.from_numpy(ytr)
    # 5-fold-free setup: hold out 20% of training rows for early stopping (time-ordered: the last 20%)
    n = len(Xt); k = int(n * 0.8)
    best, best_state, bad = np.inf, None, 0
    for ep in range(epochs):
        net.train()
        perm = torch.randperm(k)
        for i in range(0, k, 128):
            idx = perm[i:i + 128]
            opt.zero_grad()
            nn.functional.binary_cross_entropy_with_logits(net(Xt[idx]).squeeze(1), yt[idx]).backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            vl = nn.functional.binary_cross_entropy_with_logits(net(Xt[k:]).squeeze(1), yt[k:]).item()
        if vl < best - 1e-4:
            best, best_state, bad = vl, {a: v.clone() for a, v in net.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= 15:
                break
    net.load_state_dict(best_state)
    net.eval()
    return net


def line(lab, d, col="rr1_m"):
    if len(d) == 0:
        return f"| {lab} | 0 | | | |"
    hv = d.groupby(half(d["date"]))[col].mean()
    return f"| {lab} | {len(d)} | {(d[col] > 0).mean() * 100:.0f}% | {d[col].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entries", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--years", default="2020-2026")
    ap.add_argument("--split", default="2024-01-01")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--save", default="")
    ap.add_argument("--pool", default="5,50", help="day_atr,vol5_pr minimums")
    args = ap.parse_args()
    torch.set_num_threads(4)
    y0, y1 = (int(x) for x in args.years.split("-"))
    raw, paths = load_raw(args.dir, y0, y1)
    df = build(pd.read_parquet(args.entries), raw, paths)
    a, p = (float(x) for x in args.pool.split(","))
    df = df[(df["day_atr"] >= a) & (df["vol5_pr"] >= p)].reset_index(drop=True)
    if args.save:
        df.to_parquet(args.save, index=False)
    skip = {"date", "stock_id", "rr1_m", "hold_m", "tp35_m", "peak_ratio", "day_atr", "vol5_pr"}
    feats = [c for c in df.columns if c not in skip]
    fit = (df["date"] < args.split).to_numpy()
    te = ~fit
    X = prep(df, feats, fit)
    y = (df["rr1_m"] > 0).to_numpy(np.float32)
    print(f"## M 頭 → MACD 反彈紅柱轉弱做空：DL 模型（池子 ATR≥{a:g}、PR≥{p:g}）\n")
    print(f"特徵 {len(feats)} 個：{', '.join(feats)}\n")
    print(f"訓練 {fit.sum()} 筆（{y0}–{int(args.split[:4]) - 1}），測試 {te.sum()} 筆（{args.split[:4]}–{y1}）。目標 = 停損 M 頭高點 1 比 1 是否賺錢。\n")

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    import lightgbm as lgb
    preds = {}
    p_nn = np.zeros(len(df))
    for s in range(args.seeds):
        net = train_net(X[fit], y[fit], s)
        with torch.no_grad():
            p_nn += torch.sigmoid(net(torch.from_numpy(X)).squeeze(1)).numpy() / args.seeds
    preds["MLP（5 seeds 平均）"] = p_nn
    lr = LogisticRegression(C=0.3, max_iter=2000).fit(X[fit], y[fit])
    preds["Logistic"] = lr.predict_proba(X)[:, 1]
    gbm = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.02, num_leaves=7, min_child_samples=30, subsample=0.8, subsample_freq=1,
                             colsample_bytree=0.7, reg_lambda=5.0, verbose=-1, random_state=0).fit(X[fit], y[fit])
    preds["LightGBM"] = gbm.predict_proba(X)[:, 1]
    test = df[te].copy()
    print("| 模型 | 測試 AUC | 訓練 AUC |\n|---|---|---|")
    for k, pr in preds.items():
        print(f"| {k} | {roc_auc_score(y[te], pr[te]):.3f} | {roc_auc_score(y[fit], pr[fit]):.3f} |")
    print("\n### 測試期（%s–%d）依模型分數取前段\n" % (args.split[:4], y1))
    print("| 模型 / 切點（門檻取自訓練期分布） | 筆數 | 勝率 | 期望值/筆 | 正半年 |\n|---|---|---|---|---|")
    print(line("全部（不篩）", test))
    print(line("規則：反彈柱峰 < 頭部柱峰", test[test["peak_ratio"] < 1]))
    for k, pr in preds.items():
        for q in (0.5, 0.3, 0.2):
            thr = np.quantile(pr[fit], 1 - q)
            print(line(f"{k} 前 {q * 100:.0f}%", test[pr[te] >= thr]))
    imp = pd.Series(gbm.feature_importances_[:len(feats)], index=feats).sort_values(ascending=False)
    print("\n### LightGBM 特徵重要度（前 12）\n\n| 特徵 | 重要度 |\n|---|---|")
    for k, v in imp.head(12).items():
        print(f"| {k} | {v} |")
    co = pd.Series(lr.coef_[0][:len(feats)], index=feats).sort_values()
    print("\n### Logistic 係數（標準化後，負 = 越大越不利）\n\n| 特徵 | 係數 |\n|---|---|")
    for k, v in pd.concat([co.head(6), co.tail(6)]).items():
        print(f"| {k} | {v:+.2f} |")
    if args.save:
        out = test[["date", "stock_id", "rr1_m"]].copy()
        for k, pr in preds.items():
            out[k] = pr[te]
        out.to_parquet(args.save.replace(".parquet", "_scores.parquet"), index=False)


if __name__ == "__main__":
    main()
