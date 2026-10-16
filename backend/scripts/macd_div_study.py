"""Ray 2026-10-16: 「跌破大量K + 停損在高點 + 1比1，再過濾跌破大量K時 MACD 柱狀體頂背離」.
M1 MACD(12,26,9) from 09:00. At the break bar: prior peak = highest high before the run started (needs a histogram
peak > 0 within ±3 bars of it); current peak = the run high (histogram peak from run start to the break bar).
頂背離 = run high >= prior high AND current histogram peak < prior histogram peak.

    python -m scripts.macd_div_study --events 'runbreak/runbreak_202?.parquet' --trigger maxvol --pick out.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chart_trade_line import exit_point
from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost


def macd_hist(c, fast=12, slow=26, sig=9):
    s = pd.Series(c)
    macd = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    return (macd - macd.ewm(span=sig, adjust=False).mean()).to_numpy()


def classify(h, hist, rs, t):
    """rs = run start index, t = break bar index. Returns (label, prior_hi, prior_hist, cur_hist)."""
    cur_hist = hist[rs:t + 1].max()
    if rs < 6:
        return "沒有前高(當日第一波)", np.nan, np.nan, cur_hist
    pre = h[:rs]
    p = int(np.argmax(pre))
    prior_hi = pre[p]
    prior_hist = hist[max(0, p - 3):min(rs, p + 4)].max()
    run_hi = h[rs:t + 1].max()
    if run_hi < prior_hi:
        return "沒過前高", prior_hi, prior_hist, cur_hist
    if prior_hist > 0 and cur_hist < prior_hist:
        return "頂背離", prior_hi, prior_hist, cur_hist
    return "過前高、柱更高(無背離)", prior_hi, prior_hist, cur_hist


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
            t, rs = times.index(r["time"]), times.index(r["run_start"])
            i = t + 1
            o, h, l, c = (b[k].to_numpy(float) for k in "ohlc")
            hist = macd_hist(c)
            lab, phi, phh, chh = classify(h, hist, rs, t)
            sl = max(float(r["sl_near"]), 0.3)
            j, px, how = exit_point(o, h, l, c, i, -1, sl, sl)
            net = (o[i] - px) / o[i] * 100 - tick_cost(np.array([o[i]]))[0]
            rows.append({**r.to_dict(), "div": lab, "prior_hi": phi, "prior_hist": phh, "cur_hist": chh,
                         "hist_brk": hist[t], "hist_falling": bool(hist[t] < hist[t - 1] < hist[t - 2]),
                         "net1": net, "exit1": how})
    df = pd.DataFrame(rows)
    name = "量最大" if args.trigger == "maxvol" else "最長"
    print(f"# MACD 柱狀體頂背離篩選：跌破{name}K做空，停損高點、停利 1 比 1，{len(df)} 筆，2024–26，成本 2 跳\n")
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
    for lab in ("頂背離", "過前高、柱更高(無背離)", "沒過前高", "沒有前高(當日第一波)"):
        line(lab, df[df["div"] == lab])
    line("頂背離 且 柱狀體連跌 2 根", df[(df["div"] == "頂背離") & df["hist_falling"]])
    line("頂背離 且 跌破時柱狀體 < 0", df[(df["div"] == "頂背離") & (df["hist_brk"] < 0)])
    line("柱狀體連跌 2 根（不看背離）", df[df["hist_falling"]])
    df.to_parquet(K + f"runbreak_macd_{args.trigger}.parquet", index=False)
    if args.pick:
        d = df[df["div"] == "頂背離"].sort_values("date", ascending=False)
        tp10 = d[d["exit1"] == "TP"].head(10).assign(tag="tp")
        sl10 = d[d["exit1"] == "SL"].head(10).assign(tag="sl")
        pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).to_parquet(args.pick, index=False)
        print(f"\npick: {len(tp10)} TP + {len(sl10)} SL -> {args.pick}")


if __name__ == "__main__":
    main()
