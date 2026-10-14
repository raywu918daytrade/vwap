"""Ray 2026-10-14: 「再跌的空間有多少？若降低停利可以提高勝率嗎？」 for the 回跌一半再進場 variant of
scripts.m1_climax_scan (columns mfe_mid / mae_mid / g_<tp>_<sl> / gx_<tp>_<sl>). Standard filters, 2-tick cost,
train 2020-22 / test 2023-26.

    python -m scripts.climax_grid --events 'climax50g_20??.parquet'
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT
from scripts.m1_climax_scan import GRID_SL, GRID_TP
from scripts.m1vx_direction import tick_cost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    args = ap.parse_args()
    e = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.events))], ignore_index=True)
    e["stock_id"] = e["stock_id"].astype(str)
    e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00") & e["net_mid"].notna()]
    xm = tick_cost(e["entry_price_mid"].to_numpy(float)) - 0.435
    gcols = [f"g_{tp:g}_{sl:g}" for tp in GRID_TP for sl in GRID_SL]
    for c in gcols:
        e[c] = e[c] - xm
    e["tick"] = tick_cost(e["entry_price_mid"].to_numpy(float))
    print("# 無力反轉、回跌一半再進場：再跌的空間與停利×停損（PR ≥50，09:10–12:00，成本 2 跳）\n")
    for side in ("short", "long"):
        s = e[e["side"] == side]
        ok = (s["entry_pct_mid"] <= 5) & (s["hi_pct_mid"] < 9.5) if side == "short" else (s["entry_pct_mid"] >= -5) & (s["lo_pct_mid"] > -9.5)
        f = s[ok].sort_values(["date", "stock_id", "mid_time"]).groupby(["date", "stock_id"]).head(1)
        a, b = f[f["date"] < SPLIT], f[f["date"] >= SPLIT]
        lab = "上衝無力 → 回跌一半做空" if side == "short" else "下殺無力 → 反彈一半做多"
        print(f"## {lab}：訓練 {len(a)} 筆，測試 {len(b)} 筆\n")
        print("### 進場後最大有利幅度（再跌/再漲的空間，到收盤、未扣成本）\n")
        print("| 期間 | 中位數 | 平均 | ≥1% | ≥1.5% | ≥2% | ≥2.5% | ≥3% | ≥3.5% | 不利面中位數 | 不利 ≥3% |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for name, g in (("訓練 2020–22", a), ("測試 2023–26", b)):
            m, v = g["mfe_mid"], g["mae_mid"]
            cells = " | ".join(f"{(m >= k).mean() * 100:.0f}%" for k in (1, 1.5, 2, 2.5, 3, 3.5))
            print(f"| {name} | {m.median():.2f}% | {m.mean():.2f}% | {cells} | {v.median():.2f}% | {(v >= 3).mean() * 100:.0f}% |")
        print(f"\n成本中位數 {b['tick'].median():.2f}%（2 跳）。\n")
        print("### 停利 × 停損（測試 2023–26；括號為訓練）\n")
        print("| 停利 | 停損 | 勝率 | 平均/筆 | 正半年 | 停利出場 | 停損出場 | 收盤出場 | 訓練 平均/筆 |")
        print("|---|---|---|---|---|---|---|---|---|")
        for tp in GRID_TP:
            for sl in GRID_SL:
                c, x = f"g_{tp:g}_{sl:g}", f"gx_{tp:g}_{sl:g}"
                r = b[c]
                hv = b.groupby(half(b["date"]))[c].mean()
                ex = b[x].value_counts(normalize=True)
                tpl = "不停利" if tp >= 100 else f"{tp:g}%"
                print(f"| {tpl} | {sl:g}% | {(r > 0).mean() * 100:.0f}% | {r.mean():+.3f}% | {(hv > 0).sum()}/{len(hv)} | "
                      f"{ex.get(1, 0) * 100:.0f}% | {ex.get(-1, 0) * 100:.0f}% | {ex.get(0, 0) * 100:.0f}% | {a[c].mean():+.3f}% |")
        print()


if __name__ == "__main__":
    main()
