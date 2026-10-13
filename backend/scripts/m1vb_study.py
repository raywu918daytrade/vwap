"""Study of intraday M1 VWAP ±2σ reversal signals (scripts.m1_vwap_band) under the standard, with the
D1 VWAP20 band position (scripts.d1_vwap_band, z = yesterday's close vs VWAP20 in σ) as a filter.

    python -m scripts.m1vb_study --signals 'm1vb_20??.parquet' --d1 d1_vwap_band.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SLIP, SPLIT, st
from scripts.one_at_a_time import flat_dd, streaks


def one(s: pd.DataFrame, col: str) -> str:
    s = s.sort_values(["date", "entry_time", "stock_id"]).groupby("date").head(1)
    r = s[col].to_numpy(float)
    if len(r) == 0:
        return "0 | - | - | -"
    _, bl, _ = streaks([bool(x) for x in r > 0])
    return f"{len(r)} | {r.mean():+.3f}% | {flat_dd(r):.0f}% | {bl}"


def row(name: str, s: pd.DataFrame) -> str:
    a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
    g = b.groupby(half(b["date"]))["net"].mean()
    return (f"| {name} | {st(a['net'])} | {st(b['net'])} | {b['hold'].mean() if len(b) else float('nan'):+.3f}% | "
            f"{(g > 0).sum()}/{len(g)} | {one(b, 'net')} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    ap.add_argument("--d1", required=True)
    ap.add_argument("--chips", required=True)
    ap.add_argument("--tick-cost", action="store_true", help="charge 2 ticks per trade instead of 0.435%% + 0.1%% slippage")
    args = ap.parse_args()
    df = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(args.signals))], ignore_index=True)
    df["stock_id"] = df["stock_id"].astype(str)
    d1 = pd.read_parquet(args.d1, columns=["date", "stock_id", "z"])
    d1["stock_id"] = d1["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    df = df.merge(d1, on=["date", "stock_id"], how="left").merge(ch, on=["date", "stock_id"], how="left")
    df = df[df["liq_amt20"].fillna(0) >= 1]
    if args.tick_cost:
        # Ray's real cost: ~2 ticks round trip (2026-10-13), charged instead of 0.435% + slippage.
        px = df["entry_price"].to_numpy(float)
        tick = np.select([px < 10, px < 50, px < 100, px < 500, px < 1000], [0.01, 0.05, 0.1, 0.5, 1.0], 5.0)
        extra = 2 * tick / px * 100 - 0.435
        for c in ("net", "hold"):
            df[c] -= extra
        print(f"成本 = 2 跳：中位數 {np.median(extra + 0.435):.3f}%\n")
    else:
        for c in ("net", "hold"):
            df[c] -= SLIP
    head = ("| 條件 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 測試 收盤 平均 | 測試 正的半年 | "
            "測試一次一筆 筆數 | 平均(停利3.5) | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for side in ("short", "long"):
        s = df[df["side"] == side]
        s = s[(s["entry_pct"] <= 5) & (s["hi_pct"] < 9.5)] if side == "short" else s[(s["entry_pct"] >= -5) & (s["lo_pct"] > -9.5)]
        first = s.sort_values(["date", "stock_id", "time"]).groupby(["date", "stock_id"]).head(1)
        print(f"# {'做空：M1 碰到 VWAP+2σ 後收回下方' if side == 'short' else '做多：M1 碰到 VWAP−2σ 後收回上方'}（09:10-12:00，扣成本＋滑價 0.535%，每檔每天第一筆）\n")
        print(f"訊號股票日 {len(first)}；通道半寬中位數 {first['band_pct'].median():.2f}%\n")
        print(head)
        print(row("全部", first))
        zc = [("昨收 > +1σ", first["z"] > 1), ("昨收 > +2σ", first["z"] > 2), ("昨收 < −1σ", first["z"] < -1), ("昨收 < −2σ", first["z"] < -2)]
        for n, m in zc:
            print(row(n, first[m]))
        print(row("通道半寬 ≥ 1%", first[first["band_pct"] >= 1]))
        print(row("通道半寬 ≥ 1.5%", first[first["band_pct"] >= 1.5]))
        print(row("10:00 後", first[first["time"] >= "10:00"]))
        print(row("通道半寬 ≥ 1% 且 10:00 後", first[(first["band_pct"] >= 1) & (first["time"] >= "10:00")]))
        print()


if __name__ == "__main__":
    main()
