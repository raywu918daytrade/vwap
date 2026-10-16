"""Study of scripts.m1_runbreak_scan events (大量上衝後跌破最大量K／最長K的低點做空) under the standard: 09:10-12:00,
short entry <= +5% and no limit-up (long mirror), first event per stock-day per trigger, 2-tick cost, half-years,
one-at-a-time, random-entry control.

    python -m scripts.runbreak_study --events 'runbreak_202?.parquet' --ft 'ft_202?.parquet' --band d1_vwap_band.parquet
"""

import argparse
import glob

import pandas as pd

from scripts.climax_study import row
from scripts.m1vx_direction import tick_cost

MONEY = ("net", "hold", "nearsl", "tp3_nearsl", "tp25_nearsl", "tp2_nearsl", "hold_nearsl")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--ft", required=True)
    ap.add_argument("--band", required=True)
    ap.add_argument("--pr", type=float, default=50)
    args = ap.parse_args()
    e = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.events))], ignore_index=True)
    e["stock_id"] = e["stock_id"].astype(str)
    e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00")].copy()
    extra = tick_cost(e["entry_price"].to_numpy(float)) - 0.435
    for c in MONEY:
        e[c] = e[c] - extra
    print(f"# 連續大量後跌破最大量K／最長K，PR ≥{args.pr:g} 池子（09:10–12:00，每檔每天第一筆，成本 2 跳）\n")
    print(f"事件 {len(e)}（最大量K {int((e['trigger'] == 'maxvol').sum())}、最長K {int((e['trigger'] == 'longest').sum())}）；"
          f"跌破那根距高點中位數 {e['lvl_from_hi'].median():.2f}%，大量結束後 {e['bars_after'].median():.0f} 根內跌破\n")
    cols = ["date", "stock_id", "time", "minute", "day_atr", "vol5_pr", "chg_from_prev_close", "from_day_high", "from_day_low",
            "short_net", "short_hold", "long_net", "long_hold"]
    ft = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.ft))], ignore_index=True)
    ft = ft[(ft["day_atr"] >= 5) & (ft["vol5_pr"] >= args.pr) & (ft["minute"] >= 10) & (ft["minute"] <= 180)]
    ft["stock_id"] = ft["stock_id"].astype(str)
    bd = pd.read_parquet(args.band, columns=["date", "stock_id", "prev_close"])
    bd["stock_id"] = bd["stock_id"].astype(str)
    ft = ft.merge(bd, on=["date", "stock_id"])
    px = ft["prev_close"].to_numpy(float) * (1 + ft["chg_from_prev_close"].to_numpy(float) / 100)
    fx = tick_cost(px) - 0.435
    hi = ((1 + ft["chg_from_prev_close"] / 100) / (1 + ft["from_day_high"] / 100) - 1) * 100
    lo = ((1 + ft["chg_from_prev_close"] / 100) / (1 + ft["from_day_low"] / 100) - 1) * 100
    print("| 用法 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 訓練 收盤 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 正半年 | 測試 收盤 平均 | 正半年(收盤) | "
          "一次一筆 筆數 | 平均 | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for side in ("short", "long"):
        okc = (ft["chg_from_prev_close"] <= 5) & (hi < 9.5) if side == "short" else (ft["chg_from_prev_close"] >= -5) & (lo > -9.5)
        c = ft[okc].copy()
        c["net"] = c[f"{side}_net"] - fx[okc.to_numpy()]
        c["hold"] = c[f"{side}_hold"] - fx[okc.to_numpy()]
        c = c.sample(frac=1, random_state=0).groupby(["date", "stock_id"]).head(1)
        print(row(f"隨便{'空' if side == 'short' else '多'}（對照）", c))
        for trig, tl in (("maxvol", "量最大那根"), ("longest", "最長那根")):
            s = e[(e["side"] == side) & (e["trigger"] == trig)]
            ok = (s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5) if side == "short" else (s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)
            f = s[ok].sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
            what = f"大量{'上衝' if side == 'short' else '下殺'}後{'跌破' if side == 'short' else '突破'}{tl}的{'低' if side == 'short' else '高'}點 → {'做空' if side == 'short' else '做多'}"
            print(row(f"{what}，停損 3%", f))
            g = f.copy()
            g["net"], g["hold"] = g["nearsl"], g["hold_nearsl"]
            print(row(f"  停損在那段{'高' if side == 'short' else '低'}點（距離中位數 {g['sl_near'].median():.2f}%）", g))
            for tp, cc in (("3", "tp3_nearsl"), ("2.5", "tp25_nearsl"), ("2", "tp2_nearsl")):
                g2 = g.copy()
                g2["net"] = g2[cc]
                print(row(f"    同上，停利 {tp}%", g2))
            print(row("  同上，跌破在大量結束 3 根內", g[g["bars_after"] <= 3]))
            print(row("  同上，推進 ≥3%", g[g["move"].abs() >= 3]))
            print(row("  同上，10:00 後", g[g["time"] >= "10:00"]))
            print(row("  同上，跌破那根量 ≥ 大量平均", g[g["brk_vol_x"] >= 1]))
            print(row(f"  同上，{'進場時漲幅 0～5%' if side == 'short' else '進場時跌幅 0～−5%'}", g[(g["entry_pct"] >= 0) if side == "short" else (g["entry_pct"] <= 0)]))


if __name__ == "__main__":
    main()
