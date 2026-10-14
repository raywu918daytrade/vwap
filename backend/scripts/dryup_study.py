"""Seven-year study of scripts.m1_dryup_scan events (連續大量後量縮整理，突破做多／跌破做空) under the standard:
09:10-12:00, short entry <= +5% and no limit-up touched (long mirror), first event per stock-day, 2-tick cost,
train 2020-22 / test 2023-26, positive half-years, one-at-a-time, random-entry control from the feature table.

    python -m scripts.dryup_study --events 'dryup_20??.parquet' --ft 'ft_20??.parquet' --band d1_vwap_band.parquet --pr 50
"""

import argparse
import glob

import pandas as pd

from scripts.climax_study import row
from scripts.m1vx_direction import tick_cost


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
    for c in ("net", "hold", "net_boxsl", "hold_boxsl", "net_rev", "hold_rev"):
        e[c] = e[c] - extra
    print(f"# 連續大量後量縮整理，突破／跌破，2020–26，PR ≥{args.pr:g} 池子（09:10–12:00，每檔每天第一筆，成本 2 跳）\n")
    print(f"事件 {len(e)}；量縮區平均 {e['box_n'].mean():.1f} 根、寬 {e['box_rng'].median():.2f}%、量為大量的 {e['box_vol_x'].median() * 100:.0f}%\n")
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
        s = e[e["side"] == side]
        ok = (s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5) if side == "short" else (s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)
        f = s[ok].sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
        okc = (ft["chg_from_prev_close"] <= 5) & (hi < 9.5) if side == "short" else (ft["chg_from_prev_close"] >= -5) & (lo > -9.5)
        c = ft[okc].copy()
        c["net"] = c[f"{side}_net"] - fx[okc.to_numpy()]
        c["hold"] = c[f"{side}_hold"] - fx[okc.to_numpy()]
        c = c.sample(frac=1, random_state=0).groupby(["date", "stock_id"]).head(1)
        print(row(f"隨便{'空' if side == 'short' else '多'}（對照）", c))
        lab = "量縮後跌破 → 做空" if side == "short" else "量縮後突破 → 做多"
        print(row(lab, f))
        same = "up" if side == "long" else "dn"
        print(row(f"  順勢（大量{'上衝' if same == 'up' else '下殺'}後量縮再{'突破' if side == 'long' else '跌破'}）", f[f["run_dir"] == same]))
        print(row(f"  逆勢（大量{'下殺' if same == 'up' else '上衝'}後量縮再{'突破' if side == 'long' else '跌破'}）", f[f["run_dir"] != same]))
        print(row("  量縮 ≥5 根", f[f["box_n"] >= 5]))
        print(row("  量縮區窄 ≤1%", f[f["box_rng"] <= 1]))
        print(row("  突破那根量 ≥ 大量平均", f[f["brk_vol_x"] >= 1]))
        print(row("  突破那根量 < 大量平均一半", f[f["brk_vol_x"] < 0.5]))
        print(row("  10:00 後", f[f["time"] >= "10:00"]))
        print(row(f"  {'進場時漲幅 0～5%' if side == 'short' else '進場時跌幅 0～−5%'}", f[(f["entry_pct"] >= 0) if side == "short" else (f["entry_pct"] <= 0)]))
        g = f.copy()
        g["net"], g["hold"] = g["net_boxsl"], g["hold_boxsl"]
        print(row("  停損改在量縮區另一邊", g))
        r = s[(s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)] if side == "short" else s[(s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5)]
        r = r.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1).copy()
        r["net"], r["hold"] = r["net_rev"], r["hold_rev"]
        print(row(f"  反向（{'做多' if side == 'short' else '做空'}）", r))


if __name__ == "__main__":
    main()
