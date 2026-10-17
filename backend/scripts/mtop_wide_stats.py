"""M 頭 + MACD 紅柱轉弱進場 on the wider pool (scripts.m1_mtop_d1_scan outputs with day_atr / vol5_pr columns):
tables per pool (ATR>=5 PR>=50 / ATR>=3 PR>=50 / ATR>=3 PR>=30) x (紅綠紅頂背離 / 無背離 / 全部), 2024-26 and 2020-26.

    python -m scripts.mtop_wide_stats --dir /mnt/.../mtop/wide --glob 'mtopw_{y}_p*.parquet' --years 2024-2026
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chart_trade_line import exit_point
from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost
from scripts.macd_rgr import macd_hist
from scripts.mtop_macd_entry import find_entry

LOW_K = 0
LOCAL_K = 0
POOLS = [("ATR≥5、PR≥50（原本）", 5, 50), ("ATR≥3、PR≥50", 3, 50), ("ATR≥3、PR≥30", 3, 30)]


def build(dir_: str, pattern: str, y0: int, y1: int, low_k: float = 0) -> pd.DataFrame:
    rows = []
    for y in range(y0, y1 + 1):
        for f in sorted(glob.glob(f"{dir_}/" + pattern.format(y=y))):
            e = pd.read_parquet(f)
            pf = f.replace(".parquet", "_paths.parquet")
            try:
                p = pd.read_parquet(pf)
            except Exception:
                continue
            e["stock_id"] = e["stock_id"].astype(str)
            p["stock_id"] = p["stock_id"].astype(str)
            e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00") & (e["hi_pct"] < 9.5)]
            if low_k > 0 and "bar_pct" in e.columns:
                # Ray 2026-10-17: 左右低點 (L0 vs neckline) must also be within low_k bar ranges, else the M is lopsided
                l0 = e["p1_hi"] / (1 + e["rise_pct"] / 100)
                e = e[(l0 - e["neck"]).abs() / e["neck"] * 100 <= low_k * e["bar_pct"]]
            e = e.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
            g = p.groupby(["date", "stock_id"])
            for _, r in e.iterrows():
                key = (r["date"], r["stock_id"])
                rec = {k: r[k] for k in ("date", "stock_id", "rgr", "depth_pct", "sl_near", "neck", "m_hi", "prev_close", "entry_pct")}
                rec["day_atr"] = r.get("day_atr", np.nan)
                rec["vol5_pr"] = r.get("vol5_pr", np.nan)
                rec["has_path"] = key in g.groups
                if not rec["has_path"]:
                    rows.append(rec)
                    continue
                b = g.get_group(key).sort_values("time").reset_index(drop=True)
                times = b["time"].tolist()
                t = times.index(r["time"])
                o, h, l, c = (b[k].to_numpy(float) for k in ("o", "h", "l", "c"))
                # local bar range: mean high-low over the M itself (L0..H2), in % of the neckline
                i0, i2 = times.index(r["l0_time"]), times.index(r["p2_time"])
                loc = float((h[i0:i2 + 1] - l[i0:i2 + 1]).mean()) / float(r["neck"]) * 100
                l0p = float(r["p1_hi"]) / (1 + float(r["rise_pct"]) / 100)
                rec["loc_bar_pct"] = loc
                rec["top_bars_loc"] = float(r["diff_pct"]) / loc if loc > 0 else np.nan
                rec["low_bars_loc"] = abs(l0p - float(r["neck"])) / float(r["neck"]) * 100 / loc if loc > 0 else np.nan
                if LOCAL_K > 0 and not (rec["top_bars_loc"] <= LOCAL_K and rec["low_bars_loc"] <= LOCAL_K):
                    rec["skip"] = "M 不對稱（本地 K 棒）"
                    rows.append(rec)
                    continue
                hist = macd_hist(c)
                pc, neck, m_hi = float(r["prev_close"]), float(r["neck"]), float(r["m_hi"])
                u, why = find_entry(hist, h, neck, t, times)
                rec["skip"] = why
                if u is not None and u + 1 < len(o):
                    i = u + 1
                    if (h[:i].max() / pc - 1) * 100 >= 9.5:
                        rec["skip"] = "進場前碰漲停"
                    else:
                        cost = tick_cost(np.array([o[i]]))[0]
                        sl_m = max((m_hi / o[i] - 1) * 100, 0.3)
                        j, px, how = exit_point(o, h, l, c, i, -1, sl_m, sl_m)
                        rec.update({"time": times[u], "entry_price": o[i], "e_pct": (o[i] / pc - 1) * 100, "sl_m": sl_m,
                                    "rr1_m": (o[i] - px) / o[i] * 100 - cost, "exit_new": how,
                                    "hold_m": (o[i] - exit_point(o, h, l, c, i, -1, 1e6, sl_m)[1]) / o[i] * 100 - cost,
                                    "tp35_m": (o[i] - exit_point(o, h, l, c, i, -1, 3.5, sl_m)[1]) / o[i] * 100 - cost})
                rows.append(rec)
    return pd.DataFrame(rows)


def line(lab, d, col):
    d = d.dropna(subset=[col])
    if len(d) == 0:
        return f"| {lab} | 0 | | | | | |"
    w, lo = d[d[col] > 0], d[d[col] <= 0]
    hv = d.groupby(half(d["date"]))[col].mean()
    return (f"| {lab} | {len(d)} | {(d[col] > 0).mean() * 100:.0f}% | {w[col].mean():+.2f}% | {lo[col].mean():+.2f}% | "
            f"{d[col].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")


def report(df: pd.DataFrame, y0: int, y1: int) -> None:
    print(f"## M 頭（k=3{'，左右低點差 ≤3 根 K' if LOW_K else ''}{f'，兩頭差與左右低點差都 ≤{LOCAL_K:g} 根 M 本身的 K 棒' if LOCAL_K else ''}）跌破頸線 → 第一群 MACD 紅柱轉弱、未站回頸線做空，{y0}–{y1}，成本 2 跳\n")
    print(f"跌破訊號 {len(df)} 筆（{(~df['has_path']).sum()} 筆沒有存 M1 路徑，無法算進場）\n")
    ok = df[df["rr1_m"].notna()]
    for pl, a, p in POOLS:
        d = ok[(ok["day_atr"] >= a) & (ok["vol5_pr"] >= p)] if ok["day_atr"].notna().any() else ok
        print(f"### 池子 {pl}：{len(d)} 筆進場\n")
        print("| 分組 / 出場 | 筆數 | 勝率 | 平均賺 | 平均賠 | 期望值/筆 | 正半年 |")
        print("|---|---|---|---|---|---|---|")
        for gl, g in (("有紅綠紅頂背離", d[d["rgr"] == "div"]), ("無背離", d[d["rgr"] != "div"]), ("全部", d)):
            print(line(f"{gl}：停損 M 頭高點 1 比 1", g, "rr1_m"))
            print(line(f"{gl}：停損 M 頭高點，停利 3.5", g, "tp35_m"))
            print(line(f"{gl}：停損 M 頭高點，抱到收盤", g, "hold_m"))
        print()
    print("### 有背離依年（池子 ATR≥3、PR≥30，停損 M 頭高點 1 比 1）\n\n| 年 | 筆數 | 勝率 | 期望值/筆 |\n|---|---|---|---|")
    dv = ok[ok["rgr"] == "div"]
    for yy, gg in dv.groupby(dv["date"].str[:4]):
        print(f"| {yy} | {len(gg)} | {(gg['rr1_m'] > 0).mean() * 100:.0f}% | {gg['rr1_m'].mean():+.3f}% |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--glob", default="mtopw_{y}_p*.parquet")
    ap.add_argument("--years", default="2024-2026")
    ap.add_argument("--save", default="")
    ap.add_argument("--low-k", type=float, default=0, help="also require |L0 - neckline| <= this x mean bar range")
    ap.add_argument("--local-k", type=float, default=0, help="tops diff AND |L0 - neckline| <= this x mean bar range of the M's own bars")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    global LOW_K, LOCAL_K
    LOW_K = args.low_k
    LOCAL_K = args.local_k
    df = build(args.dir, args.glob, y0, y1, args.low_k)
    if args.save:
        df.to_parquet(args.save, index=False)
    report(df, y0, y1)


if __name__ == "__main__":
    main()
