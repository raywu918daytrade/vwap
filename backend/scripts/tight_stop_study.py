"""Ray 2026-10-16: 「看對的好像都一路下跌，若把停損放在進場那根或前根的高點，停利不變」 — run-break shorts with the
stop at the high of the break bar (前根) / the entry bar / the higher of both, take-profit kept at the run-high
distance (1x) or fixed 3.5%. Stop at the entry bar's high is checked from the next bar on.

    python -m scripts.tight_stop_study --events 'runbreak/runbreak_202?.parquet' --trigger maxvol [--pick out.parquet --variant prev]
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost


def short_exit(o, h, l, c, i, stop, tp_p, start):
    """Short at o[i]; stop/tp checked from bar `start` on. Returns (bar, price, how)."""
    for t in range(start, len(c)):
        if h[t] >= stop:
            return t, stop, "SL"
        if l[t] <= tp_p:
            return t, tp_p, "TP"
    return len(c) - 1, c[-1], "close"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--trigger", default="maxvol")
    ap.add_argument("--pick", default="")
    ap.add_argument("--variant", default="prev")
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
            trades.append((r, b, t + 1))
    name = "量最大" if args.trigger == "maxvol" else "最長"
    print(f"# 停損放近一點：大量上衝後跌破{name}那根低點做空，{len(trades)} 筆，2024–26，成本 2 跳\n")
    print("| 停損 | 停利 | 停損距離中位 | 勝率 | 平均賺 | 平均賠 | 停利/停損/收盤 | 期望值/筆 | 正半年 |")
    print("|---|---|---|---|---|---|---|---|---|")
    variants = (("prev", "前一根(跌破K)高點"), ("entry", "進場那根高點"), ("both", "兩根較高者"), ("run", "上衝段高點(原本)"))
    for var, vl in variants:
        for tpk, tl in (("1x", "高點到進場距離(1倍)"), ("3.5", "固定 3.5%")):
            rows = []
            for r, b, i in trades:
                o, h, l, c = (b[k].to_numpy(float) for k in "ohlc")
                e0 = o[i]
                run_sl = max(float(r["sl_near"]), 0.3)
                if var == "prev":
                    stop, start = h[i - 1], i
                elif var == "entry":
                    stop, start = h[i], i + 1
                elif var == "both":
                    stop, start = max(h[i - 1], h[i]), i + 1
                else:
                    stop, start = e0 * (1 + run_sl / 100), i
                stop = max(stop, e0 * 1.003)
                sl_pct = (stop - e0) / e0 * 100
                tp_pct = run_sl if tpk == "1x" else 3.5
                j, px, how = short_exit(o, h, l, c, i, stop, e0 * (1 - tp_pct / 100), start)
                net = (e0 - px) / e0 * 100 - tick_cost(np.array([e0]))[0]
                rows.append({**r.to_dict(), "sl_pct": sl_pct, "tp_pct": tp_pct, "net_t": net, "exit_t": how, "exit_bar": b["time"][j]})
            df = pd.DataFrame(rows)
            w, lo = df[df["net_t"] > 0], df[df["net_t"] <= 0]
            hv = df.groupby(half(df["date"]))["net_t"].mean()
            ex = df["exit_t"].value_counts(normalize=True)
            print(f"| {vl} | {tl} | {df['sl_pct'].median():.2f}% | {(df['net_t'] > 0).mean() * 100:.0f}% | {w['net_t'].mean():+.2f}% | "
                  f"{lo['net_t'].mean():+.2f}% | {ex.get('TP', 0) * 100:.0f}/{ex.get('SL', 0) * 100:.0f}/{ex.get('close', 0) * 100:.0f}% | "
                  f"{df['net_t'].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")
            if args.pick and var == args.variant and tpk == "1x":
                df = df.sort_values("date", ascending=False)
                tp10 = df[df["exit_t"] == "TP"].head(10).assign(tag="tp")
                sl10 = df[df["exit_t"] == "SL"].head(10).assign(tag="sl")
                pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).to_parquet(args.pick, index=False)


if __name__ == "__main__":
    main()
