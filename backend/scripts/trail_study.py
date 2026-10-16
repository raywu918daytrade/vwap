"""Ray 2026-10-16: 「那加入移動停損呢？」 — trailing stops for the run-break shorts (scripts.m1_runbreak_scan events with
M1 paths). Initial stop at the run high; after activation (immediately, or once the trade is up 0.5x / 1x the stop
distance) the stop trails the lowest low by a fixed distance (1 / 1.5 / 2% or the stop distance); optional move to
break-even at activation; no take-profit (hold to close) unless --tp.

    python -m scripts.trail_study --events 'runbreak/runbreak_202?.parquet' --trigger maxvol
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost


def trail_short(o, h, l, c, i, sl_pct, trail_pct, activate_x, breakeven, tp):
    """Short at o[i]; returns net % before cost and exit code (1 TP, -1 stop, 0 close)."""
    e0 = o[i]
    stop = e0 * (1 + sl_pct / 100)
    tp_p = e0 * (1 - tp / 100) if tp else None
    active = activate_x == 0
    low = e0
    for t in range(i, len(c)):
        # stop check first (conservative): bar high reaches the stop
        if h[t] >= stop:
            return -(stop - e0) / e0 * 100, -1
        if tp_p is not None and l[t] <= tp_p:
            return (e0 - tp_p) / e0 * 100, 1
        low = min(low, l[t])
        gain = (e0 - low) / e0 * 100
        if not active and gain >= activate_x * sl_pct:
            active = True
            if breakeven:
                stop = min(stop, e0)
        if active:
            stop = min(stop, low * (1 + trail_pct / 100))
    return (e0 - c[-1]) / e0 * 100, 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--trigger", default="maxvol")
    ap.add_argument("--tp", type=float, default=0)
    args = ap.parse_args()
    K = "/mnt/project-files/vwap-backtest/key-price/"
    trades = []
    for p in sorted(glob.glob(K + args.events)):
        e = pd.read_parquet(p)
        paths = pd.read_parquet(p.replace(".parquet", "_paths.parquet"))
        e["stock_id"] = e["stock_id"].astype(str)
        paths["stock_id"] = paths["stock_id"].astype(str)
        f = e[(e["side"] == "short") & (e["trigger"] == args.trigger) & (e["time"] >= "09:10") & (e["time"] <= "12:00")
              & (e["entry_pct"] <= 5) & (e["hi_pct"] < 9.5)]
        f = f.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
        g = paths.groupby(["date", "stock_id"])
        for _, r in f.iterrows():
            if (r["date"], r["stock_id"]) not in g.groups:
                continue
            b = g.get_group((r["date"], r["stock_id"])).sort_values("time").reset_index(drop=True)
            t = b["time"].tolist().index(r["time"])
            trades.append((r["date"], tuple(b[k].to_numpy(float) for k in "ohlc"), t + 1, max(float(r["sl_near"]), 0.3)))
    print(f"# 移動停損：大量上衝後跌破{'量最大' if args.trigger == 'maxvol' else '最長'}那根低點做空，停損先放高點，{len(trades)} 筆，2024–26，成本 2 跳\n")
    print("| 啟動時機 | 追蹤距離 | 啟動時移到損益兩平 | 勝率 | 平均賺 | 平均賠 | 停損出場 | 期望值/筆 | 正半年 |")
    print("|---|---|---|---|---|---|---|---|---|")
    base = []
    for d, (o, h, l, c), i, sl in trades:
        n, x = trail_short(o, h, l, c, i, sl, 1e9, 1e9, False, 3.5)
        base.append((d, n - tick_cost(np.array([o[i]]))[0], x))
    bd = pd.DataFrame(base, columns=["date", "net", "exit"])
    def line(lab, df):
        w, lo = df[df["net"] > 0], df[df["net"] <= 0]
        hv = df.groupby(half(df["date"]))["net"].mean()
        print(f"| {lab} | {(df['net'] > 0).mean() * 100:.0f}% | {w['net'].mean():+.2f}% | {lo['net'].mean():+.2f}% | "
              f"{(df['exit'] == -1).mean() * 100:.0f}% | {df['net'].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")
    line("不移動（停損高點、停利 3.5%） | — | —", bd)
    for act, al in ((0, "立刻"), (0.5, "獲利 0.5 倍停損距離後"), (1.0, "獲利 1 倍後")):
        for tr, tl in ((1.0, "1%"), (1.5, "1.5%"), (2.0, "2%"), (None, "= 停損距離")):
            for be in ((False,) if act == 0 else (False, True)):
                rows = []
                for d, (o, h, l, c), i, sl in trades:
                    n, x = trail_short(o, h, l, c, i, sl, tr if tr else sl, act, be, args.tp)
                    rows.append((d, n - tick_cost(np.array([o[i]]))[0], x))
                line(f"{al} | {tl} | {'是' if be else '否'}", pd.DataFrame(rows, columns=["date", "net", "exit"]))


if __name__ == "__main__":
    main()
