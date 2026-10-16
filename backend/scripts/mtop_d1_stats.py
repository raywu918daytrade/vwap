"""Win-rate tables for the M1 M-top (D1 detector, k-bar tolerance) short, from scripts.m1_mtop_d1_scan outputs.
Standard filters: 09:10-12:00, entry <= +5% vs prev close, no limit-up touched (hi < 9.5), first per stock-day;
cost re-based from 0.435% to 2 ticks round trip at the entry price.

    python -m scripts.mtop_d1_stats --k 3 --years 2024-2026 [--pick out.parquet]
"""

import argparse

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.m1vx_direction import tick_cost

K = "/mnt/project-files/vwap-backtest/key-price/mtop/d1k/"
VARIANTS = [("rr1_nearsl", "exit_rr1", "跌破頸線下一根進，停損 M 頭高點，1 比 1"),
            ("tp35_nearsl", None, "同上，停利 3.5 / 停損 M 頭高點"),
            ("hold_nearsl", None, "同上，停損 M 頭高點，抱到收盤"),
            ("net", "exit", "固定停利 3.5 / 停損 3"),
            ("hold", None, "抱到收盤（停損 3）"),
            ("v_rr1_nearsl", None, "等再跌破 VWAP 才進，停損 M 頭高點，1 比 1"),
            ("v_net", None, "等再跌破 VWAP 才進，停利 3.5 / 停損 3"),
            ("v_hold_nearsl", None, "等再跌破 VWAP 才進，停損 M 頭高點，抱到收盤")]


def load(k: int, y0: int, y1: int) -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(K + f"mtopd1_k{k}_{y}.parquet") for y in range(y0, y1 + 1)], ignore_index=True)
    d["stock_id"] = d["stock_id"].astype(str)
    d = d[(d["time"] >= "09:10") & (d["time"] <= "12:00") & (d["entry_pct"] <= 5) & (d["hi_pct"] < 9.5)]
    d = d.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1).copy()
    extra = tick_cost(d["entry_price"].to_numpy(float)) - 0.435
    for col, _, _ in VARIANTS:
        if col.startswith("v_"):
            ok = d["vwap_time"].ne("")
            ev = d["entry_price"] * (1 + d["v_entry_pct"] / 100) / (1 + d["entry_pct"] / 100)
            d[col] = np.where(ok, d[col] - (tick_cost(ev.fillna(1).to_numpy(float)) - 0.435), np.nan)
        else:
            d[col] = d[col] - extra
    return d


def line(lab: str, d: pd.DataFrame, col: str, excol: str | None) -> str:
    d = d.dropna(subset=[col])
    if len(d) == 0:
        return f"| {lab} | 0 | | | | | | |"
    w, lo = d[d[col] > 0], d[d[col] <= 0]
    hv = d.groupby(half(d["date"]))[col].mean()
    exs = "—"
    if excol:
        ex = d[excol].value_counts(normalize=True)
        exs = f"{ex.get(1, 0) * 100:.0f}/{ex.get(-1, 0) * 100:.0f}/{ex.get(0, 0) * 100:.0f}%"
    return (f"| {lab} | {len(d)} | {(d[col] > 0).mean() * 100:.0f}% | {w[col].mean():+.2f}% | {lo[col].mean():+.2f}% | {exs} | "
            f"{d[col].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--years", default="2024-2026")
    ap.add_argument("--pick", default="")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    d = load(args.k, y0, y1)
    print(f"## M 頭（D1 偵測器，誤差 {args.k} 根 K）跌破頸線做空，{y0}–{y1}，{len(d)} 筆，成本 2 跳\n")
    print("| 進出場 | 筆數 | 勝率 | 平均賺 | 平均賠 | 停利/停損/收盤 | 期望值/筆 | 正半年 |")
    print("|---|---|---|---|---|---|---|---|")
    for col, excol, lab in VARIANTS:
        print(line(lab, d, col, excol))
    print("\n### 依年（停損 M 頭高點 1 比 1）\n\n| 年 | 筆數 | 勝率 | 期望值/筆 |\n|---|---|---|---|")
    for y, g in d.groupby(d["date"].str[:4]):
        print(f"| {y} | {len(g)} | {(g['rr1_nearsl'] > 0).mean() * 100:.0f}% | {g['rr1_nearsl'].mean():+.3f}% |")
    print("\n### 分組（停損 M 頭高點 1 比 1）\n\n| 分組 | 筆數 | 勝率 | 期望值/筆 | 正半年 |\n|---|---|---|---|---|")
    hb = d["time"].str[:2]
    groups = [("跌破時已在 VWAP 下", d[d["below_vwap"]]), ("跌破時還在 VWAP 上", d[~d["below_vwap"]]),
              ("09:10–09:59", d[hb == "09"]), ("10:00–10:59", d[hb == "10"]), ("11:00–12:00", d[hb >= "11"]),
              ("頸線深度 ≥2.5%", d[d["depth_pct"] >= 2.5]), ("頸線深度 <2.5%", d[d["depth_pct"] < 2.5]),
              ("M 頭高點 ≥ 開盤 +3%", d[d["m_vs_open"] >= 3]), ("M 頭高點 < 開盤 +3%", d[d["m_vs_open"] < 3]),
              ("停損距離 ≥2%", d[d["sl_near"] >= 2]), ("停損距離 <2%", d[d["sl_near"] < 2]),
              ("紅綠紅頂背離", d[d["rgr"] == "div"]), ("無背離", d[d["rgr"] != "div"]),
              ("漲幅 ≤0%（進場低於昨收）", d[d["entry_pct"] <= 0]), ("漲幅 >0%", d[d["entry_pct"] > 0])]
    for lab, g in groups:
        if len(g) == 0:
            continue
        hv = g.groupby(half(g["date"]))["rr1_nearsl"].mean()
        print(f"| {lab} | {len(g)} | {(g['rr1_nearsl'] > 0).mean() * 100:.0f}% | {g['rr1_nearsl'].mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} |")
    if args.pick:
        s = d.sort_values("date", ascending=False)
        s["div"] = "M 頭"
        s["brk_label"] = "跌破頸線"
        tp10 = s[s["exit_rr1"] == 1].head(10).assign(tag="tp")
        sl10 = s[s["exit_rr1"] == -1].head(10).assign(tag="sl")
        pd.concat([tp10.iloc[::-1], sl10.iloc[::-1]]).to_parquet(args.pick, index=False)
        print(f"\npick: {len(tp10)} TP + {len(sl10)} SL")


if __name__ == "__main__":
    main()
