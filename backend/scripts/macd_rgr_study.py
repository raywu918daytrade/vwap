"""Red-green-red MACD divergence (scripts.macd_rgr) applied to the run-break shorts (stop at run high, TP 1x).

    python -m scripts.macd_rgr_study --events 'runbreak/runbreak_20??.parquet' --trigger maxvol --pick out.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chart_trade_line import exit_point
from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost
from scripts.macd_rgr import macd_hist, rgr_divergence


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--trigger", default="maxvol")
    ap.add_argument("--pick", default="")
    args = ap.parse_args()
    K = "/mnt/project-files/vwap-backtest/key-price/"
    rows = []
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
            times = b["time"].tolist()
            t = times.index(r["time"])
            i = t + 1
            o, h, l, c = (b[k].to_numpy(float) for k in "ohlc")
            hist = macd_hist(c)
            lab, i1, i2, q1, q2 = rgr_divergence(h, hist, t)
            sl = max(float(r["sl_near"]), 0.3)
            j, px, how = exit_point(o, h, l, c, i, -1, sl, sl)
            net = (o[i] - px) / o[i] * 100 - tick_cost(np.array([o[i]]))[0]
            rows.append({**r.to_dict(), "div": lab, "p1_time": times[i1] if i1 >= 0 else "", "p2_time": times[i2] if i2 >= 0 else "",
                         "q1_time": times[q1] if q1 >= 0 else "", "q2_time": times[q2] if q2 >= 0 else "",
                         "hist_t": hist[t], "net1": net, "exit1": how})
    df = pd.DataFrame(rows)
    yrs = f"{df['date'].min()[:4]}–{df['date'].max()[2:4]}"
    name = "量最大" if args.trigger == "maxvol" else "最長"
    print(f"# 紅綠紅柱狀體頂背離：跌破{name}K做空，停損高點、停利 1 比 1，{len(df)} 筆，{yrs}，成本 2 跳\n")
    print("| 跌破時的 MACD 狀態 | 筆數 | 勝率 | 平均賺 | 平均賠 | 停利/停損/收盤 | 期望值/筆 | 正半年 |")
    print("|---|---|---|---|---|---|---|---|")

    def line(lab, d):
        if len(d) == 0:
            return
        w, lo = d[d["net1"] > 0], d[d["net1"] <= 0]
        hv = d.groupby(half(d["date"]))["net1"].mean()
        ex = d["exit1"].value_counts(normalize=True)
        print(f"| {lab} | {len(d)} | {(d['net1'] > 0).mean() * 100:.0f}% | {w['net1'].mean():+.2f}% | {lo['net1'].mean():+.2f}% | "
              f"{ex.get('TP', 0) * 100:.0f}/{ex.get('SL', 0) * 100:.0f}/{ex.get('close', 0) * 100:.0f}% | "
              f"{d['net1'].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")

    line("全部", df)
    for lab, name in (("div", "紅綠紅頂背離"), ("nodiv", "紅綠紅、第二紅柱更高（無背離）"), ("lowerhigh", "紅綠紅、價格沒過前高"), ("one", "只有一段紅柱")):
        line(name, df[df["div"] == lab])
    d = df[df["div"] == "div"]
    line("頂背離 且 跌破時柱狀體已轉綠", d[d["hist_t"] < 0])
    line("頂背離 且 跌破時柱狀體仍紅", d[d["hist_t"] >= 0])
    df.to_parquet(K + f"runbreak_rgr_{args.trigger}.parquet", index=False)
    if args.pick:
        d = d.sort_values("date", ascending=False)
        tp10 = d[d["exit1"] == "TP"].head(10).assign(tag="tp")
        sl10 = d[d["exit1"] == "SL"].head(10).assign(tag="sl")
        pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).to_parquet(args.pick, index=False)
        print(f"\npick: {len(tp10)} TP + {len(sl10)} SL")


if __name__ == "__main__":
    main()
