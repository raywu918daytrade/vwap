"""Ray 2026-10-16: 「跌破 VWAP 後再進」 — run-break short with red-green-red MACD divergence (scripts.macd_rgr), but the
entry waits for the first M1 close below VWAP at or after the break bar (before 12:00 and before price takes out the
run high). Stop at the run high, take-profit 1x that distance from the new entry; also TP 3.5 / SL 3.

    python -m scripts.rgr_vwap_study --events 'runbreak/runbreak_20??.parquet' --trigger maxvol --pick out.parquet
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
            o, h, l, c, vw = (b[k].to_numpy(float) for k in ("o", "h", "l", "c", "vwap"))
            hist = macd_hist(c)
            lab, i1, i2, q1, q2 = rgr_divergence(h, hist, t)
            run_hi = o[t + 1] * (1 + float(r["sl_near"]) / 100)
            # wait for the first close below VWAP
            u, why = None, ""
            for k in range(t, len(c) - 1):
                if times[k] > "12:00":
                    why = "12:00 前沒跌破 VWAP"
                    break
                if h[k] > run_hi and k > t:
                    why = "先過高點"
                    break
                if c[k] < vw[k]:
                    u = k
                    break
            rec = {**r.to_dict(), "div": lab, "p1_time": times[i1] if i1 >= 0 else "", "p2_time": times[i2] if i2 >= 0 else "",
                   "q1_time": times[q1] if q1 >= 0 else "", "q2_time": times[q2] if q2 >= 0 else "",
                   "vwap_time": times[u] if u is not None else "", "skip": why, "wait_bars": (u - t) if u is not None else np.nan}
            # original entry (next open after the break) for comparison
            i0 = t + 1
            sl0 = max(float(r["sl_near"]), 0.3)
            j, px, how = exit_point(o, h, l, c, i0, -1, sl0, sl0)
            rec["net0"] = (o[i0] - px) / o[i0] * 100 - tick_cost(np.array([o[i0]]))[0]
            if u is not None:
                i = u + 1
                sl = max((run_hi / o[i] - 1) * 100, 0.3)
                j, px, how = exit_point(o, h, l, c, i, -1, sl, sl)
                cost = tick_cost(np.array([o[i]]))[0]
                rec.update({"time": times[u], "entry_price": o[i], "sl_near": sl, "net1": (o[i] - px) / o[i] * 100 - cost, "exit1": how,
                            "already_below": c[t] < vw[t]})
                j, px, how = exit_point(o, h, l, c, i, -1, 3.5, 3.0)
                rec["net35"] = (o[i] - px) / o[i] * 100 - cost
            rows.append(rec)
    df = pd.DataFrame(rows)
    yrs = f"{df['date'].min()[:4]}–{df['date'].max()[2:4]}"
    name = "量最大" if args.trigger == "maxvol" else "最長"
    print(f"# 跌破{name}K ＋ 紅綠紅頂背離，等收盤跌破 VWAP 再做空，停損高點，{yrs}，成本 2 跳\n")

    def line(lab, d, col):
        d = d.dropna(subset=[col])
        if len(d) == 0:
            return
        w, lo = d[d[col] > 0], d[d[col] <= 0]
        hv = d.groupby(half(d["date"]))[col].mean()
        ex = d["exit1"].value_counts(normalize=True) if col == "net1" else None
        exs = f"{ex.get('TP', 0) * 100:.0f}/{ex.get('SL', 0) * 100:.0f}/{ex.get('close', 0) * 100:.0f}%" if ex is not None else "—"
        print(f"| {lab} | {len(d)} | {(d[col] > 0).mean() * 100:.0f}% | {w[col].mean():+.2f}% | {lo[col].mean():+.2f}% | {exs} | "
              f"{d[col].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")

    for grp, gl in (("div", "紅綠紅頂背離"), ("nodiv", "無背離（第二紅柱更高）"), ("all", "全部跌破（不看背離）")):
        d = df if grp == "all" else df[df["div"] == grp]
        print(f"\n## {gl}：{len(d)} 筆訊號，其中 {d['vwap_time'].ne('').sum()} 筆等到跌破 VWAP"
              f"（{(d['skip'] == '先過高點').sum()} 筆先過高點作罷，{(d['skip'] == '12:00 前沒跌破 VWAP').sum()} 筆 12:00 前沒跌破）\n")
        print("| 進場 | 筆數 | 勝率 | 平均賺 | 平均賠 | 停利/停損/收盤 | 期望值/筆 | 正半年 |")
        print("|---|---|---|---|---|---|---|---|")
        line("跌破大量K下一根就進（原本）", d, "net0")
        line("等跌破 VWAP 再進，停損高點、1 比 1", d, "net1")
        line("　其中跌破K時已在 VWAP 下（同原本）", d[d["already_below"] == True], "net1")
        line("　其中要再等（真的多等了）", d[d["already_below"] == False], "net1")
        line("等跌破 VWAP 再進，停利 3.5 / 停損 3", d, "net35")
    df.to_parquet(K + f"runbreak_rgr_vwap_{args.trigger}.parquet", index=False)
    if args.pick:
        d = df[(df["div"] == "div") & df["vwap_time"].ne("")].sort_values("date", ascending=False)
        d["div"] = "紅綠紅頂背離"
        tp10 = d[d["exit1"] == "TP"].head(10).assign(tag="tp")
        sl10 = d[d["exit1"] == "SL"].head(10).assign(tag="sl")
        pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).to_parquet(args.pick, index=False)
        print(f"\npick: {len(tp10)} TP + {len(sl10)} SL")


if __name__ == "__main__":
    main()
