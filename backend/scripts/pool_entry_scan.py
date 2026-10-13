"""Entry scan inside the volatility pool Ray confirmed (2026-10-13): base ATR14>=5 & opening-5-min vol PR>=50, plus
(opening 5-min range >= 3% or ATR >= 7). Six short and six long M5 triggers x four time windows, standard rules
(short entry <= +5% / long >= -5%, no limit touched so far, next-M1-open entry, SL 3%, TP 3.5% or hold to close),
cost = 2 ticks, first signal per stock-day, random-entry control per window.

    python -m scripts.pool_entry_scan --ft 'ft_20??.parquet' --band d1_vwap_band.parquet --chips chips_daily.parquet
"""

import argparse
import glob

import numpy as np
import pandas as pd

from scripts.chips_model import half
from scripts.d1_rebound_short import SPLIT, st
from scripts.m1vx_direction import tick_cost
from scripts.one_at_a_time import flat_dd, streaks

WINDOWS = [("09:10-10:00", 10, 60), ("10:00-11:00", 60, 120), ("11:00-12:00", 120, 180), ("12:00-13:00", 180, 240)]


def one(s: pd.DataFrame) -> str:
    s = s.sort_values(["date", "minute", "stock_id"]).groupby("date").head(1)
    r = s["net"].to_numpy(float)
    if len(r) == 0:
        return "0 | - | - | -"
    _, bl, _ = streaks([bool(x) for x in r > 0])
    return f"{len(r)} | {r.mean():+.3f}% | {flat_dd(r):.0f}% | {bl}"


def row(name: str, s: pd.DataFrame) -> str:
    a, b = s[s["date"] < SPLIT], s[s["date"] >= SPLIT]
    g = b.groupby(half(b["date"]))["net"].mean()
    gh = b.groupby(half(b["date"]))["hold"].mean()
    return (f"| {name} | {st(a['net'])} | {st(b['net'])} | {(g > 0).sum()}/{len(g)} | "
            f"{b['hold'].mean() if len(b) else float('nan'):+.3f}% | {(gh > 0).sum()}/{len(gh)} | {one(b)} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("--ft", "--band", "--chips"):
        ap.add_argument(a, required=True)
    args = ap.parse_args()
    cols = ["date", "stock_id", "minute", "day_atr", "vol5_pr", "open5_rng", "chg_from_prev_close", "gap_open", "chg_from_open",
            "from_day_high", "from_day_low", "vs_vwap", "vwap_cross_up_5m", "vwap_cross_dn_5m", "m5_ret", "prev_m5_ret", "prev_m5_red",
            "prev_m5_black", "break_prev_m5_low", "break_prev_m5_high", "m5_vol_ratio", "long_net", "long_hold", "short_net", "short_hold"]
    df = pd.concat([pd.read_parquet(p, columns=cols) for p in sorted(glob.glob(args.ft))], ignore_index=True)
    df = df[(df["day_atr"] >= 5) & (df["vol5_pr"] >= 50) & ((df["open5_rng"] >= 3) | (df["day_atr"] >= 7)) & (df["minute"] >= 10) & (df["minute"] <= 240)]
    df["stock_id"] = df["stock_id"].astype(str)
    bd = pd.read_parquet(args.band, columns=["date", "stock_id", "prev_close"])
    bd["stock_id"] = bd["stock_id"].astype(str)
    ch = pd.read_parquet(args.chips, columns=["stock_id", "date", "liq_amt20"])
    ch["stock_id"] = ch["stock_id"].astype(str)
    df = df.merge(bd, on=["date", "stock_id"]).merge(ch, on=["date", "stock_id"], how="left")
    df = df[df["liq_amt20"].fillna(0) >= 1].sort_values(["date", "stock_id", "minute"]).reset_index(drop=True)
    px = df["prev_close"].to_numpy(float) * (1 + df["chg_from_prev_close"].to_numpy(float) / 100)
    extra = tick_cost(px) - 0.435
    key = [df["date"], df["stock_id"]]
    hi = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_high"] / 100) - 1) * 100
    lo = ((1 + df["chg_from_prev_close"] / 100) / (1 + df["from_day_low"] / 100) - 1) * 100
    t_up = (hi >= 9.5).astype(int).groupby(key).cummax().astype(bool)
    t_dn = (lo <= -9.5).astype(int).groupby(key).cummax().astype(bool)
    n_days = df.groupby(["date", "stock_id"]).ngroups
    print(f"# 高波動池（PR≥50、ATR≥5，且 開盤5分振幅≥3% 或 ATR≥7）日內進場掃描；股票日 {n_days}，成本 2 跳（中位數 {np.median(extra + 0.435):.3f}%）\n")
    below, above = df["vs_vwap"] < 0, df["vs_vwap"] > 0
    red_black = (df["prev_m5_red"] == 1) & (df["m5_ret"] < 0)
    black_red = (df["prev_m5_black"] == 1) & (df["m5_ret"] > 0)
    trig = {
        "short": [
            ("下穿 VWAP（5 分內）", (df["vwap_cross_dn_5m"] == 1) & below),
            ("紅接黑 且 跌破前 M5 低", red_black & (df["break_prev_m5_low"] == 1)),
            ("創當日新低 且 在 VWAP 下", (df["from_day_low"] >= -0.05) & below),
            ("離當日高回落 ≥ 2% 且 在 VWAP 下", (df["from_day_high"] <= -2) & below),
            ("開低走低（開低、低於開盤、跌破前 M5 低）", (df["gap_open"] < 0) & (df["chg_from_open"] < 0) & (df["break_prev_m5_low"] == 1)),
            ("大量黑 K（量 ≥ 2 倍）在 VWAP 下", (df["m5_ret"] < 0) & (df["m5_vol_ratio"] >= 2) & below),
        ],
        "long": [
            ("上穿 VWAP（5 分內）", (df["vwap_cross_up_5m"] == 1) & above),
            ("黑接紅 且 突破前 M5 高", black_red & (df["break_prev_m5_high"] == 1)),
            ("創當日新高 且 在 VWAP 上", (df["from_day_high"] >= -0.05) & above),
            ("離當日低反彈 ≥ 2% 且 在 VWAP 上", (df["from_day_low"] >= 2) & above),
            ("開高走高（開高、高於開盤、突破前 M5 高）", (df["gap_open"] > 0) & (df["chg_from_open"] > 0) & (df["break_prev_m5_high"] == 1)),
            ("大量紅 K（量 ≥ 2 倍）在 VWAP 上", (df["m5_ret"] > 0) & (df["m5_vol_ratio"] >= 2) & above),
        ],
    }
    head = ("| 時段 | 進場 | 訓練 筆數 | 勝率 | 停利3.5 平均 | 測試 筆數 | 勝率 | 停利3.5 平均 | 正半年 | 測試 收盤 平均 | 正半年(收盤) | "
            "一次一筆 筆數 | 平均 | 回落 | 最多連輸 |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for side in ("short", "long"):
        df["net"] = df[f"{side}_net"] - extra
        df["hold"] = df[f"{side}_hold"] - extra
        ok = (df["chg_from_prev_close"] <= 5) & ~t_up if side == "short" else (df["chg_from_prev_close"] >= -5) & ~t_dn
        print(f"## {'做空' if side == 'short' else '做多'}\n")
        print(head)
        for wn, m0, m1 in WINDOWS:
            w = ok & (df["minute"] >= m0) & (df["minute"] < m1)
            ctl = df[w].sample(frac=1, random_state=0).groupby(["date", "stock_id"]).head(1)
            print(f"| {wn} | 隨便進（對照） " + row("", ctl)[3:])
            for tn, tm in trig[side]:
                s = df[w & tm].groupby(["date", "stock_id"]).head(1)
                print(f"| {wn} | {tn} " + row("", s)[3:])
        print()


if __name__ == "__main__":
    main()
