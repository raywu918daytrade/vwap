"""Ray 2026-10-16: M 頭跌破頸線後，「第一個 MACD 柱狀體群最大後降低時，而且沒有超過 M 頭的低點（頸線）再做空」.
From the neckline-break bar t (scripts.m1_mtop_d1_scan output): take the first positive (red) MACD-histogram block that
starts after t (the bounce), wait until its bars peak and the next bar is lower (still > 0), and require that no high
from t to that bar exceeded the neckline. Entry next open. Exits: stop at the M high 1:1 / 3.5 / hold; stop at the
neckline (= the bounce cap) 1:1 / hold; fixed 3.5/3.

    python -m scripts.mtop_macd_entry --k 3 --years 2024-2026 [--pick out.parquet]
"""

import argparse

import numpy as np
import pandas as pd

from scripts.chart_trade_line import exit_point
from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost
from scripts.macd_rgr import macd_hist

K = "/mnt/project-files/vwap-backtest/key-price/mtop/d1k/"


def find_entry(hist, h, neck, t, times):
    """Return (u, why): u = bar where the first red block after t turns down, or None."""
    n = len(hist)
    s = None
    for k in range(t + 1, n - 1):
        if times[k] > "12:00":
            return None, "12:00 前沒出現"
        if h[k] > neck:
            return None, "反彈站回頸線"
        if hist[k] > 0:
            if s is None:
                if hist[k - 1] <= 0:  # a red block that starts after the break (the bounce)
                    s = k
            elif hist[k] < hist[k - 1]:
                return k, ""
        elif s is not None:
            # block ended without a down-bar after its peak (single-bar block): treat the end as the turn
            return k, ""
    return None, "12:00 前沒出現"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--years", default="2024-2026")
    ap.add_argument("--pick", default="")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    rows = []
    for y in range(y0, y1 + 1):
        e = pd.read_parquet(K + f"mtopd1_k{args.k}_{y}.parquet")
        p = pd.read_parquet(K + f"mtopd1_k{args.k}_{y}_paths.parquet")
        e["stock_id"] = e["stock_id"].astype(str)
        p["stock_id"] = p["stock_id"].astype(str)
        e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00") & (e["entry_pct"] <= 5) & (e["hi_pct"] < 9.5)]
        e = e.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
        g = p.groupby(["date", "stock_id"])
        for _, r in e.iterrows():
            key = (r["date"], r["stock_id"])
            if key not in g.groups:
                continue
            b = g.get_group(key).sort_values("time").reset_index(drop=True)
            times = b["time"].tolist()
            t = times.index(r["time"])
            o, h, l, c = (b[k].to_numpy(float) for k in ("o", "h", "l", "c"))
            hist = macd_hist(c)
            pc = float(r["prev_close"])
            neck = float(r["neck"])
            m_hi = float(r["m_hi"])
            u, why = find_entry(hist, h, neck, t, times)
            rec = {**r.to_dict(), "brk_time": r["time"], "skip": why, "div": "M 頭", "brk_label": "MACD 紅柱轉弱"}
            if u is not None:
                i = u + 1
                if i >= len(o):
                    continue
                cost = tick_cost(np.array([o[i]]))[0]
                hi_sofar = h[:i].max()
                bounce = h[t + 1:u + 1].max() if u > t else h[u]
                if (o[i] / pc - 1) * 100 > 5 or (hi_sofar / pc - 1) * 100 >= 9.5:
                    rec["skip"] = "進場時漲幅超標"
                else:
                    sl_m = max((m_hi / o[i] - 1) * 100, 0.3)
                    sl_n = max((neck / o[i] - 1) * 100, 0.3)
                    rec.update({"time": times[u], "entry_price": o[i], "entry_pct": (o[i] / pc - 1) * 100, "sl_near": sl_m,
                                "sl_neck": sl_n, "wait_bars": u - t, "bounce_pct": (bounce / o[i] - 1) * 100, "neck_gap_pct": (neck / o[i] - 1) * 100})
                    j, px, how = exit_point(o, h, l, c, i, -1, sl_m, sl_m)
                    rec["rr1_m"], rec["exit_new"] = (o[i] - px) / o[i] * 100 - cost, how
                    rec["tp35_m"] = (o[i] - exit_point(o, h, l, c, i, -1, 3.5, sl_m)[1]) / o[i] * 100 - cost
                    rec["hold_m"] = (o[i] - exit_point(o, h, l, c, i, -1, 1e6, sl_m)[1]) / o[i] * 100 - cost
                    rec["rr1_n"] = (o[i] - exit_point(o, h, l, c, i, -1, sl_n, sl_n)[1]) / o[i] * 100 - cost
                    rec["rr2_n"] = (o[i] - exit_point(o, h, l, c, i, -1, 2 * sl_n, sl_n)[1]) / o[i] * 100 - cost
                    rec["hold_n"] = (o[i] - exit_point(o, h, l, c, i, -1, 1e6, sl_n)[1]) / o[i] * 100 - cost
                    rec["net35"] = (o[i] - exit_point(o, h, l, c, i, -1, 3.5, 3.0)[1]) / o[i] * 100 - cost
            rows.append(rec)
    df = pd.DataFrame(rows)
    ok = df[df["rr1_m"].notna()]
    print(f"## M 頭跌破頸線後，等第一群 MACD 紅柱轉弱、未站回頸線再做空（誤差 {args.k} 根 K），{y0}–{y1}，成本 2 跳\n")
    print(f"{len(df)} 筆跌破訊號，其中 {len(ok)} 筆等到進場；作罷：" + "、".join(f"{k} {v}" for k, v in df["skip"].value_counts().items() if k))
    print(f"\n等待時間中位數 {ok['wait_bars'].median():.0f} 分鐘；進場價離頸線中位數 {ok['neck_gap_pct'].median():.2f}%、離 M 頭高點中位數 {ok['sl_near'].median():.2f}%\n")
    print("| 進出場 | 筆數 | 勝率 | 平均賺 | 平均賠 | 期望值/筆 | 正半年 |")
    print("|---|---|---|---|---|---|---|")

    def line(lab, d, col):
        d = d.dropna(subset=[col])
        if len(d) == 0:
            return
        w, lo = d[d[col] > 0], d[d[col] <= 0]
        hv = d.groupby(half(d["date"]))[col].mean()
        print(f"| {lab} | {len(d)} | {(d[col] > 0).mean() * 100:.0f}% | {w[col].mean():+.2f}% | {lo[col].mean():+.2f}% | "
              f"{d[col].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")

    line("新：停損 M 頭高點，1 比 1", ok, "rr1_m")
    line("新：停損 M 頭高點，停利 3.5", ok, "tp35_m")
    line("新：停損 M 頭高點，抱到收盤", ok, "hold_m")
    line("新：停損頸線，1 比 1", ok, "rr1_n")
    line("新：停損頸線，1 比 2", ok, "rr2_n")
    line("新：停損頸線，抱到收盤", ok, "hold_n")
    line("新：固定停利 3.5 / 停損 3", ok, "net35")
    print("\n### 依年（停損 M 頭高點 1 比 1）\n\n| 年 | 筆數 | 勝率 | 期望值/筆 |\n|---|---|---|---|")
    for yy, gg in ok.groupby(ok["date"].str[:4]):
        print(f"| {yy} | {len(gg)} | {(gg['rr1_m'] > 0).mean() * 100:.0f}% | {gg['rr1_m'].mean():+.3f}% |")
    print("\n### 分組（停損 M 頭高點 1 比 1）\n\n| 分組 | 筆數 | 勝率 | 期望值/筆 | 正半年 |\n|---|---|---|---|---|")
    groups = [("等 ≤10 分鐘", ok[ok["wait_bars"] <= 10]), ("等 11–30 分鐘", ok[(ok["wait_bars"] > 10) & (ok["wait_bars"] <= 30)]),
              ("等 >30 分鐘", ok[ok["wait_bars"] > 30]), ("進場離頸線 <1%", ok[ok["neck_gap_pct"] < 1]), ("進場離頸線 ≥1%", ok[ok["neck_gap_pct"] >= 1]),
              ("進場在昨收之上", ok[ok["entry_pct"] > 0]), ("進場在昨收之下", ok[ok["entry_pct"] <= 0]),
              ("紅綠紅頂背離（跌破時）", ok[ok["rgr"] == "div"]), ("無背離", ok[ok["rgr"] != "div"])]
    for lab, gg in groups:
        if len(gg) == 0:
            continue
        hv = gg.groupby(half(gg["date"]))["rr1_m"].mean()
        print(f"| {lab} | {len(gg)} | {(gg['rr1_m'] > 0).mean() * 100:.0f}% | {gg['rr1_m'].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")
    df.to_parquet(K + f"mtop_macd_entry_k{args.k}_{y0}-{y1}.parquet", index=False)
    if args.pick:
        s = ok.sort_values("date", ascending=False)
        tp10 = s[s["exit_new"] == "TP"].head(10).assign(tag="tp")
        sl10 = s[s["exit_new"] == "SL"].head(10).assign(tag="sl")
        pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).to_parquet(args.pick, index=False)
        print(f"\npick: {len(tp10)} TP + {len(sl10)} SL")


if __name__ == "__main__":
    main()
