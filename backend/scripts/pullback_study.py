"""Seven-year study of scripts.m1_pullback_scan events (連續大量後量縮回落一半) under the standard: 09:10-12:00,
long entry >= -5% and no limit-down (short mirror), first event per stock-day, 2-tick cost, train 2020-22 / test
2023-26, positive half-years, one-at-a-time, random-entry control from the feature table.

    python -m scripts.pullback_study --events 'pullback_20??.parquet' --ft 'ft_20??.parquet' --band d1_vwap_band.parquet
"""

import argparse
import glob

import pandas as pd

from scripts.climax_study import row
from scripts.m1vx_direction import tick_cost


def prep(e: pd.DataFrame, pre: str) -> pd.DataFrame:
    """Rows with the given entry prefix ('' touch / 'cf_' confirm), cost at 2 ticks, standard columns."""
    f = e[e[f"{pre}net"].notna()].copy()
    extra = tick_cost(f[f"{pre}entry_price"].to_numpy(float)) - 0.435
    money = ("net", "hold", "net_farsl", "hold_farsl", "net_rev", "hold_rev", "rev_nearsl", "rev_hold_nearsl", "rev_tp2_nearsl", "rev_tp15_nearsl")
    for c in money + ("exit", "entry_pct", "hi_pct", "lo_pct", "sl_far", "sl_near"):
        if f"{pre}{c}" in f:
            f[c] = f[f"{pre}{c}"] - (extra if c in money else 0)
    if pre:
        f["time"] = f["cf_time"]
    return f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--ft", required=True)
    ap.add_argument("--band", required=True)
    ap.add_argument("--pr", type=float, default=50)
    args = ap.parse_args()
    e = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.events))], ignore_index=True)
    e["stock_id"] = e["stock_id"].astype(str)
    e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00")]
    print(f"# 連續大量後量縮回落一半，2020–26，PR ≥{args.pr:g} 池子（09:10–12:00，每檔每天第一筆，成本 2 跳）\n")
    print(f"事件 {len(e)}：大量上衝後回落 {(e['run_dir'] == 'up').sum()}，大量下殺後反彈 {(e['run_dir'] == 'dn').sum()}；"
          f"回落平均 {e['pb_bars'].mean():.1f} 根、回落量為大量的 {e['pb_vol_x'].median() * 100:.0f}%；"
          f"回落後有確認反轉 K 的 {e['cf_net'].notna().mean() * 100:.0f}%\n")
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
    for run_dir in ("up", "dn"):
        with_side = "long" if run_dir == "up" else "short"
        against = "short" if run_dir == "up" else "long"
        for side in (with_side, against):
            okc = (ft["chg_from_prev_close"] <= 5) & (hi < 9.5) if side == "short" else (ft["chg_from_prev_close"] >= -5) & (lo > -9.5)
            c = ft[okc].copy()
            c["net"] = c[f"{side}_net"] - fx[okc.to_numpy()]
            c["hold"] = c[f"{side}_hold"] - fx[okc.to_numpy()]
            c = c.sample(frac=1, random_state=0).groupby(["date", "stock_id"]).head(1)
            print(row(f"隨便{'空' if side == 'short' else '多'}（對照）", c))
            what = "大量上衝後量縮回落一半" if run_dir == "up" else "大量下殺後量縮反彈一半"
            how = "順勢" if side == with_side else "逆勢"
            for pre, ent in (("", "碰到一半下一根進場"), ("cf_", "回落後第一根反轉 K 再進場")):
                f = prep(e[e["run_dir"] == run_dir], pre)
                if side != with_side:
                    f["net"], f["hold"] = f["net_rev"], f["hold_rev"]
                ok = (f["entry_pct"] <= 5) & (f["hi_pct"] < 9.5) if side == "short" else (f["entry_pct"] >= -5) & (f["lo_pct"] > -9.5)
                f = f[ok].sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
                lab = f"{what}，{how}{'做多' if side == 'long' else '做空'}，{ent}"
                print(row(lab, f))
                if side != with_side and "rev_nearsl" in f:
                    g = f.copy()
                    g["net"], g["hold"] = g["rev_nearsl"], g["rev_hold_nearsl"]
                    print(row(f"  停損改在那段{'高點' if side == 'short' else '低點'}（距離中位數 {g['sl_near'].median():.2f}%）", g))
                    for tp, c in (("2", "rev_tp2_nearsl"), ("1.5", "rev_tp15_nearsl")):
                        g2 = g.copy()
                        g2["net"] = g2[c]
                        print(row(f"    同上，停利 {tp}%", g2))
                    print(row("  同上（停損在高/低點），10:00 後", g[g["time"] >= "10:00"]))
                    print(row("  同上（停損在高/低點），推進 ≥3%", g[g["move"].abs() >= 3]))
                if side == with_side:
                    g = f.copy()
                    g["net"], g["hold"] = g["net_farsl"], g["hold_farsl"]
                    print(row("  停損改在那段起點", g))
                    print(row("  回落量 ≤ 大量 1/3", f[f["pb_vol_x"] <= 1 / 3]))
                    print(row("  推進 ≥3%", f[f["move"].abs() >= 3]))
                    print(row("  大量 ≥4 根", f[f["k"] >= 4]))
                    print(row("  10:00 後", f[f["time"] >= "10:00"]))
                    print(row(f"  {'進場時漲幅 0～5%' if side == 'short' else '進場時跌幅 0～−5%'}", f[(f["entry_pct"] >= 0) if side == "short" else (f["entry_pct"] <= 0)]))
                    print(row("  進場時在 VWAP 之上" if side == "long" else "  進場時在 VWAP 之下", f[(f["vs_vwap"] > 0) if side == "long" else (f["vs_vwap"] < 0)]))


if __name__ == "__main__":
    main()
