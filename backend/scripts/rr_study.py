"""Ray 2026-10-16: 「不要用 3.5 改成 高點 - 進場點 用 1 比 1」 — take-profit as a multiple of the stop distance
(run high − entry) for the run-break shorts (scripts.m1_runbreak_scan events + M1 paths). Prints the table for
0.5/1/1.5/2/3x and fixed 3.5%, and writes a pick file (10 most recent TP + 10 SL) for --mult.

    python -m scripts.rr_study --events 'runbreak/runbreak_202?.parquet' --trigger maxvol --mult 1 --pick out.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chart_trade_line import exit_point
from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--trigger", default="maxvol")
    ap.add_argument("--mult", type=float, default=1.0)
    ap.add_argument("--pick", default="")
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
            trades.append((r, tuple(b[k].to_numpy(float) for k in "ohlc"), t + 1, max(float(r["sl_near"]), 0.3)))
    name = "量最大" if args.trigger == "maxvol" else "最長"
    print(f"# 停利＝停損距離的倍數：大量上衝後跌破{name}那根低點做空，停損放高點（中位數 {np.median([s for *_, s in trades]):.2f}%），"
          f"{len(trades)} 筆，2024–26，成本 2 跳\n")
    print("| 停利 | 勝率 | 平均賺 | 平均賠 | 停利出場 | 停損出場 | 收盤出場 | 期望值/筆 | 正半年 |")
    print("|---|---|---|---|---|---|---|---|---|")

    def run(mult, fixed=None):
        rows = []
        for r, (o, h, l, c), i, sl in trades:
            tp = fixed if fixed else mult * sl
            j, px, how = exit_point(o, h, l, c, i, -1, tp, sl)
            net = (o[i] - px) / o[i] * 100 - tick_cost(np.array([o[i]]))[0]
            rows.append({**r.to_dict(), "tp_pct": tp, "net_rr": net, "exit_rr": how, "exit_time": None})
        return pd.DataFrame(rows)

    for mult, lab in ((0.5, "0.5 倍"), (1.0, "1 倍"), (1.5, "1.5 倍"), (2.0, "2 倍"), (3.0, "3 倍"), (None, "固定 3.5%")):
        df = run(mult, 3.5 if mult is None else None)
        w, lo = df[df["net_rr"] > 0], df[df["net_rr"] <= 0]
        hv = df.groupby(half(df["date"]))["net_rr"].mean()
        ex = df["exit_rr"].value_counts(normalize=True)
        print(f"| {lab} | {(df['net_rr'] > 0).mean() * 100:.0f}% | {w['net_rr'].mean():+.2f}% | {lo['net_rr'].mean():+.2f}% | "
              f"{ex.get('TP', 0) * 100:.0f}% | {ex.get('SL', 0) * 100:.0f}% | {ex.get('close', 0) * 100:.0f}% | "
              f"{df['net_rr'].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")
        if args.pick and mult == args.mult:
            df = df.sort_values("date", ascending=False)
            tp10 = df[df["exit_rr"] == "TP"].head(10).assign(tag="tp")
            sl10 = df[df["exit_rr"] == "SL"].head(10).assign(tag="sl")
            pk = pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).drop(columns=["exit_time"])
            pk.to_parquet(args.pick, index=False)
            print(f"\npick -> {args.pick}: {len(tp10)} TP + {len(sl10)} SL")


if __name__ == "__main__":
    main()
