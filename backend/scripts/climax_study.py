"""Six-year study of scripts.m1_climax_scan events (連續大量後的無力反轉) under the standard: 09:10-12:00, short
entry <= +5% and no limit-up touched (long mirror), first event per stock-day, 2-tick cost, train 2020-22 / test
2023-26, positive half-years, one-at-a-time, and a random-entry control from the feature table in the same pool.

    python -m scripts.climax_study --events 'climax_20??.parquet' --ft 'ft_20??.parquet' --band d1_vwap_band.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT, st
from scripts.m1vx_direction import tick_cost
from scripts.one_at_a_time import flat_dd, streaks


def one(s: pd.DataFrame) -> str:
    s = s.sort_values(["date", "time", "stock_id"]).groupby("date").head(1)
    r = s["net"].to_numpy(float)
    if len(r) == 0:
        return "0 | - | - | -"
    _, bl, _ = streaks([bool(x) for x in r > 0])
    return f"{len(r)} | {r.mean():+.3f}% | {flat_dd(r):.0f}% | {bl}"


def row(name: str, s: pd.DataFrame) -> str:
    a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
    g = b.groupby(half(b["date"]))["net"].mean()
    gh = b.groupby(half(b["date"]))["hold"].mean()
    return (f"| {name} | {st(a['net'])} | {a['hold'].mean() if len(a) else float('nan'):+.3f}% | {st(b['net'])} | {(g > 0).sum()}/{len(g)} | "
            f"{b['hold'].mean() if len(b) else float('nan'):+.3f}% | {(gh > 0).sum()}/{len(gh)} | {one(b)} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--ft", required=True)
    ap.add_argument("--band", required=True)
    ap.add_argument("--pr", type=float, default=90, help="opening-volume PR floor of the pool (control uses the same)")
    args = ap.parse_args()
    e = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.events))], ignore_index=True)
    e["stock_id"] = e["stock_id"].astype(str)
    e = e[(e["time"] >= "09:10") & (e["time"] <= "12:00")]
    extra = tick_cost(e["entry_price"].to_numpy(float)) - 0.435
    for c in ("net", "hold", "net_rev", "hold_rev", "net_runsl"):
        if c in e:
            e[c] = e[c] - extra
    if "net_mid" in e:
        xm = tick_cost(e["entry_price_mid"].fillna(1).to_numpy(float)) - 0.435
        for c in ("net_mid", "hold_mid", "net_mid_runsl"):
            e[c] = e[c] - xm
    print(f"# 連續大量後的無力反轉，2020–26，PR ≥{args.pr:g} 池子（09:10–12:00，每檔每天第一筆，成本 2 跳）\n")
    print(f"事件 {len(e)}；上衝後無力 {(e['side'] == 'short').sum()}，下殺後無力 {(e['side'] == 'long').sum()}\n")
    # control: random M5 row per stock-day in the same pool/window
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
    head = ("| 用法 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 訓練 收盤 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 正半年 | 測試 收盤 平均 | 正半年(收盤) | "
            "一次一筆 筆數 | 平均 | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    print(head)
    for side in ("short", "long"):
        s = e[e["side"] == side]
        ok = (s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5) if side == "short" else (s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)
        f = s[ok].sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
        lab = "大量上衝後無力 → 做空" if side == "short" else "大量下殺後無力 → 做多"
        okc = (ft["chg_from_prev_close"] <= 5) & (hi < 9.5) if side == "short" else (ft["chg_from_prev_close"] >= -5) & (lo > -9.5)
        c = ft[okc].copy()
        c["net"] = c[f"{side}_net"] - fx[okc.to_numpy()]
        c["hold"] = c[f"{side}_hold"] - fx[okc.to_numpy()]
        c = c.sample(frac=1, random_state=0).groupby(["date", "stock_id"]).head(1)
        print(row(f"隨便{'空' if side == 'short' else '多'}（對照）", c))
        print(row(lab, f))
        print(row("  推進 ≥2%", f[f["move"].abs() >= 2]))
        print(row("  推進 ≥3%", f[f["move"].abs() >= 3]))
        print(row("  大量 ≥4 根", f[f["k"] >= 4]))
        print(row("  反轉那根量 ≤ 大量平均 1/3", f[f["rev_vol_x"] <= 1 / 3]))
        print(row("  反轉在大量後 1 根內", f[f["bars_after"] <= 1]))
        print(row("  10:00 後", f[f["time"] >= "10:00"]))
        print(row(f"  {'進場時漲幅 0～5%' if side == 'short' else '進場時跌幅 0～−5%'}", f[(f["entry_pct"] >= 0) if side == "short" else (f["entry_pct"] <= 0)]))
        r = s[(s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)] if side == "short" else s[(s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5)]
        r = r.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1).copy()
        r["net"], r["hold"] = r["net_rev"], r["hold_rev"]
        print(row(f"  反向（{'做多' if side == 'short' else '做空'}）", r))
        if "net_mid" in e:
            v = f.copy()
            v["net"], v["hold"] = v["net_runsl"], v["hold"]
            print(row("  同上，停損改在那段高/低點", v))
            okm = (s["entry_pct_mid"] <= 5) & (s["hi_pct_mid"] < 9.5) if side == "short" else (s["entry_pct_mid"] >= -5) & (s["lo_pct_mid"] > -9.5)
            w = s[okm & s["net_mid"].notna()].sort_values(["date", "stock_id", "mid_time"]).groupby(["date", "stock_id"]).head(1).copy()
            w["time"] = w["mid_time"]
            w["net"], w["hold"] = w["net_mid"], w["hold_mid"]
            print(row("回跌一半再進場（停損 3%）", w))
            w2 = w.copy()
            w2["net"] = w2["net_mid_runsl"]
            print(row("回跌一半再進場，停損在那段高/低點", w2))
            print(row("  回跌一半，推進 ≥3%", w[w["move"].abs() >= 3]))
            print(row("  回跌一半，大量 ≥4 根", w[w["k"] >= 4]))
            print(f"| （無力 K 後有回到一半的比例：{s['net_mid'].notna().mean() * 100:.0f}%） | | | | | | | | | | | | | | |")


if __name__ == "__main__":
    main()
